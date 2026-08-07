"""Token pricing, the one billing write per turn, and the spend caps decided against the ledger."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
from ufo.schema import tables
from ufo.schema.records import Usage, ledger_id_for

MICRO_USD_PER_USD = 1_000_000

TOKENS_DIMENSION = "tokens"
EGRESS_DIMENSION = "egress"
SANDBOX_TOKENS_DIMENSION = "sandbox_tokens"
IMAGES_DIMENSION = "images"

CapScope = Literal["workspace", "member", "agent"]
WORKSPACE_SCOPE: CapScope = "workspace"
MEMBER_SCOPE: CapScope = "member"
AGENT_SCOPE: CapScope = "agent"

OnBreach = Literal["park", "reject"]
PARK: OnBreach = "park"
REJECT: OnBreach = "reject"

SpendOutcome = Literal["allow", "park", "reject"]
ALLOW: SpendOutcome = "allow"

CAP_PRESENCE_TTL_SECONDS = 5.0
CAP_PRESENCE_CACHE_MAX = 4096
_no_applicable_caps: dict[tuple[UUID, UUID | None, UUID], float] = {}


def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool:
    """Connectionless fast-path: True only when a recent decision found no cap applies to this
    (workspace, member, agent), within a short TTL. The per-round enforcement then skips its DB
    round-trip — the common no-caps deploy pays nothing per round. Keyed by the exact triple so a
    cap on another member never suppresses this one; a newly-set cap takes effect within the TTL."""
    expiry = _no_applicable_caps.get((workspace_id, member_id, agent_id))
    return expiry is not None and expiry > time.monotonic()


def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None:
    """Remember for the TTL that no cap applies to this triple. Evict expired entries once the map
    is full so a long-lived serve seeing many distinct triples never grows it without bound —
    correctness never rests on the cache, so a purge that frees nothing simply lets it drift over
    the soft bound until entries age out."""
    now = time.monotonic()
    if len(_no_applicable_caps) >= CAP_PRESENCE_CACHE_MAX:
        for expired in [k for k, expiry in _no_applicable_caps.items() if expiry <= now]:
            del _no_applicable_caps[expired]
    _no_applicable_caps[key] = now + CAP_PRESENCE_TTL_SECONDS


def _total_tokens(usage: Usage) -> int:
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )


def _prompt_tokens(usage: Usage) -> int:
    """The tokens the provider read to answer, cached or not — the denominator of the cache share a
    terminal frame renders."""
    return usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens


async def record_turn_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
    attempt: str = "",
    pricing: Pricing = CORE_PRICING,
) -> None:
    """One billing write per turn per run attempt; select-then-insert is replay-safe because DBOS
    re-executes a given attempt sequentially, never concurrently with itself. A turn parked mid-run
    and resumed spends under a fresh attempt (workflow id), so each partial burn is billed once and
    the ledger reflects the true total the provider charged — never a lost burn, never a double.

    The row carries the burn's prompt split beside its total, so a terminal frame's cache share is a
    read of the same row the tokens, cost and model come off rather than a second account of the
    same spend."""
    total = _total_tokens(usage)
    if total == 0:
        return
    ledger_id = ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, attempt)
    billed = await connection.execute(
        sa.select(tables.ledger.c.id).where(tables.ledger.c.id == ledger_id)
    )
    if billed.one_or_none() is not None:
        return
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=TOKENS_DIMENSION,
            amount=total,
            prompt_tokens=_prompt_tokens(usage),
            cache_read_tokens=usage.cache_read_tokens,
            priced_micro_usd=pricing.micro_usd(model, usage),
            model=model,
            price_digest=pricing.digest,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


@dataclass(frozen=True, slots=True)
class TurnCost:
    """What a turn spent under one ledger dimension: the billed tokens, their micro-USD, the model
    that burned them, and the share of the prompt that was served from cache."""

    tokens: int
    micro_usd: int
    model: str
    cache_percent: int


async def read_turn_cost(
    connection: AsyncConnection, turn_id: UUID, dimension: str
) -> TurnCost | None:
    """What a turn spent, summed across its run attempts; None when nothing was billed. A
    parked-then-resumed turn has one ledger row per attempt, so the terminal cost is their total —
    the true provider charge — and the cache share is the cached part of the whole prompt, computed
    from the split those same rows carry rather than from any in-memory account of the burn.

    `dimension` names which of the turn's spends that is: ufo's own rounds bill `tokens` host-side,
    while a loop that makes its model calls from inside the sandbox has them metered onto the same
    turn by the egress proxy under `sandbox_tokens`."""
    row = (
        await connection.execute(
            sa.select(
                sa.func.sum(tables.ledger.c.amount),
                sa.func.sum(tables.ledger.c.priced_micro_usd),
                sa.func.max(tables.ledger.c.model),
                sa.func.sum(tables.ledger.c.prompt_tokens),
                sa.func.sum(tables.ledger.c.cache_read_tokens),
            ).where((tables.ledger.c.turn_id == turn_id) & (tables.ledger.c.dimension == dimension))
        )
    ).one()
    if row[0] is None:
        return None
    prompt_tokens, cache_read_tokens = int(row[3]), int(row[4])
    return TurnCost(
        tokens=int(row[0]),
        micro_usd=int(row[1]),
        model=row[2],
        cache_percent=round(100 * cache_read_tokens / prompt_tokens) if prompt_tokens else 0,
    )


async def record_workspace_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    model: str,
    usage: Usage,
    pricing: Pricing = CORE_PRICING,
) -> None:
    """Bill a background job's metered model call to the workspace, not a turn: one priced `tokens`
    row with `turn_id` NULL, stamped with the model and price digest exactly as a turn's tokens are.
    The row carries a fresh id, so each genuine completion is billed once — there is no DBOS step
    checkpoint around a job's model call, so a workflow replay re-invokes the provider (a real
    charge) and bills that invocation, never a phantom double or a lost burn. It lands in the
    workspace spend total and every workspace-scoped cap window (which sum by `workspace_id`), and
    is excluded from per-member and per-agent attribution (which join through `turn` — a NULL FK
    drops out), because a job's spend belongs to no member or agent."""
    total = _total_tokens(usage)
    if total == 0:
        return
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=uuid4(),
            workspace_id=workspace_id,
            turn_id=None,
            dimension=TOKENS_DIMENSION,
            amount=total,
            prompt_tokens=_prompt_tokens(usage),
            cache_read_tokens=usage.cache_read_tokens,
            priced_micro_usd=pricing.micro_usd(model, usage),
            model=model,
            price_digest=pricing.digest,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def record_egress_request(
    connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int = 1
) -> None:
    """Meter one sandbox egress request as an `egress` ledger row per turn, incremented atomically
    so concurrent proxy writes never lose a count. A request COUNT priced at zero, never a dollar
    charge, under a dimension distinct from `tokens`: it neither re-bills the model tokens
    `record_turn_usage` bills at terminal nor moves a spend cap. Keyed with an empty attempt — the
    egress proxy has no run attempt, and a turn's egress count is per turn, not per run — so the id
    can never collide with a token row (different dimension) and a parked-then-resumed turn keeps
    accumulating into the one row."""
    ledger_id = ledger_id_for(workspace_id, turn_id, EGRESS_DIMENSION)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    await connection.execute(
        insert(tables.ledger)
        .values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=EGRESS_DIMENSION,
            amount=amount,
            priced_micro_usd=0,
            model="",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={"amount": tables.ledger.c.amount + amount, "updated_at": sa.func.now()},
        )
    )


async def record_sandbox_tokens(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
    pricing: Pricing = CORE_PRICING,
) -> None:
    """Meter a model call the sandbox made through the egress proxy as a `sandbox_tokens` ledger row
    per turn, its tokens and priced cost accumulated atomically so several in-sandbox calls on one
    turn never lose a burn. Disjoint from the host turn loop's `tokens` bill: that path runs the
    model host-side and never touches the proxy, so the two sources never overlap and metering here
    is additive, not a double-count. Priced through the deploy's merged `pricing` (core plus every
    provider-contributed rate, `CORE_PRICING` when none) and stamped with its digest — the same
    table and stamp the host turn's `tokens` bill uses, so a contributed slug is billed at its real
    rate and sandbox rows reconcile with turn rows by digest. Keyed with an empty attempt under a
    dimension distinct from `tokens`, so its id can never collide with the host row
    `record_turn_usage` writes for the same turn."""
    total = _total_tokens(usage)
    if total == 0:
        return
    priced = pricing.micro_usd(model, usage)
    ledger_id = ledger_id_for(workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    await connection.execute(
        insert(tables.ledger)
        .values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=SANDBOX_TOKENS_DIMENSION,
            amount=total,
            priced_micro_usd=priced,
            model=model,
            price_digest=pricing.digest,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={
                "amount": tables.ledger.c.amount + total,
                "priced_micro_usd": tables.ledger.c.priced_micro_usd + priced,
                "updated_at": sa.func.now(),
            },
        )
    )


async def record_image_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    images: int,
    micro_usd: int,
) -> None:
    """Meter generated images as an `images` ledger row per turn: `amount` counts the images and
    `priced_micro_usd` is what they cost, both accumulated atomically so several generations on one
    turn never lose one. The caller prices them, because an image model is not a `ModelSpec` and its
    unit is an image (or a megapixel, or an output image token) rather than the four token counters
    `ModelPrice` rates — the provider extension reads the charge off its own response and books it
    here, and the row carries no `price_digest` because no pinned rate table describes it. Keyed
    with an empty attempt under a dimension distinct from `tokens`, so its id can never collide
    with the row `record_turn_usage` writes for the same turn, and a parked-then-resumed turn keeps
    accumulating into the one row."""
    ledger_id = ledger_id_for(workspace_id, turn_id, IMAGES_DIMENSION)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    await connection.execute(
        insert(tables.ledger)
        .values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=IMAGES_DIMENSION,
            amount=images,
            priced_micro_usd=micro_usd,
            model=model,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={
                "amount": tables.ledger.c.amount + images,
                "priced_micro_usd": tables.ledger.c.priced_micro_usd + micro_usd,
                "updated_at": sa.func.now(),
            },
        )
    )


EXPORT_SETTLE_MARGIN_SECONDS = 900


@dataclass(frozen=True, slots=True)
class UsageExport:
    """One unshipped usage delta for an external billing consumer: the ledger row's growth between
    `from_amount` and the amount at mint, frozen so a re-send after an unacknowledged delivery is
    byte-identical under the same `(ledger_id, from_amount)` dedup key — the consumer's
    at-least-once retry can therefore never double- or under-bill."""

    ledger_id: UUID
    from_amount: int
    amount: int
    priced_micro_usd: int
    dimension: str
    model: str
    price_digest: str | None
    turn_id: UUID | None
    byok: bool
    occurred_at: datetime


async def mint_usage_exports(
    connection: AsyncConnection,
    workspace_id: UUID,
    consumer: str,
    floor: datetime,
    key_slot_for: Callable[[str], str | None],
) -> None:
    """Freeze the consumer's unshipped usage growth into `ledger_export` intent rows. This is the
    export seam's settlement knowledge, kept beside the writers that define it: a `tokens` row is
    insert-once and settles at creation; a `sandbox_tokens` or `images` row accumulates until its
    turn is terminal, and `EXPORT_SETTLE_MARGIN_SECONDS` past `turn.updated_at` only bounds how
    often a late egress-proxy write costs an extra top-up intent — a row that grows after minting
    mints a further intent from the prior high-water mark, so no growth is ever lost to timing.
    Egress rows (a zero-priced request count) never export. Usage settling before `floor` never
    mints — the consumer's backfill bound. Idempotent: an intent's `(consumer, ledger_id,
    from_amount)` key makes concurrent or replayed mints collapse onto one frozen row.

    Each intent freezes its `byok` label too: host-side `tokens` usage whose model's provider
    key slot (`key_slot_for` — the same resolution `client_for` applies) is stored by the
    workspace is the workspace's spend; everything else — providers keyed from platform env,
    `sandbox_tokens`, whose egress proxy injects the platform key, and `images`, whose model is not
    in the registry for a slot to be resolved from — is the platform's. Frozen at mint, a re-send
    carries the label of the key state that served the usage, never the drain-time state."""
    now = datetime.now(UTC)
    settle_cutoff = now - timedelta(seconds=EXPORT_SETTLE_MARGIN_SECONDS)
    stored_slots = {
        row.slot
        for row in (
            await connection.execute(
                sa.select(tables.credential.c.slot).where(
                    tables.credential.c.workspace_id == workspace_id
                )
            )
        ).all()
    }
    latest = (
        sa.select(
            tables.ledger_export.c.ledger_id,
            sa.func.max(tables.ledger_export.c.to_amount).label("to_amount"),
            sa.func.max(tables.ledger_export.c.to_micro_usd).label("to_micro_usd"),
        )
        .where(tables.ledger_export.c.consumer == consumer)
        .group_by(tables.ledger_export.c.ledger_id)
        .subquery()
    )
    growth = (
        await connection.execute(
            sa.select(
                tables.ledger.c.id,
                tables.ledger.c.workspace_id,
                tables.ledger.c.amount,
                tables.ledger.c.priced_micro_usd,
                tables.ledger.c.dimension,
                tables.ledger.c.model,
                tables.ledger.c.updated_at,
                sa.func.coalesce(latest.c.to_amount, 0).label("from_amount"),
                sa.func.coalesce(latest.c.to_micro_usd, 0).label("from_micro_usd"),
            )
            .select_from(
                tables.ledger.outerjoin(
                    tables.turn, tables.turn.c.id == tables.ledger.c.turn_id
                ).outerjoin(latest, latest.c.ledger_id == tables.ledger.c.id)
            )
            .where(
                tables.ledger.c.workspace_id == workspace_id,
                sa.or_(latest.c.ledger_id.is_(None), tables.ledger.c.amount > latest.c.to_amount),
                sa.or_(
                    (tables.ledger.c.dimension == TOKENS_DIMENSION)
                    & (tables.ledger.c.created_at >= floor),
                    tables.ledger.c.dimension.in_((SANDBOX_TOKENS_DIMENSION, IMAGES_DIMENSION))
                    & tables.turn.c.terminal.isnot(None)
                    & (tables.turn.c.updated_at <= settle_cutoff)
                    & (tables.turn.c.updated_at >= floor),
                ),
            )
        )
    ).all()
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    for row in growth:
        await connection.execute(
            insert(tables.ledger_export)
            .values(
                consumer=consumer,
                ledger_id=row.id,
                from_amount=row.from_amount,
                workspace_id=row.workspace_id,
                to_amount=row.amount,
                from_micro_usd=row.from_micro_usd,
                to_micro_usd=row.priced_micro_usd,
                byok=(
                    row.dimension == TOKENS_DIMENSION
                    and (slot := key_slot_for(row.model)) is not None
                    and slot in stored_slots
                ),
                occurred_at=row.updated_at,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    tables.ledger_export.c.consumer,
                    tables.ledger_export.c.ledger_id,
                    tables.ledger_export.c.from_amount,
                ]
            )
        )


async def read_pending_usage_exports(
    connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int
) -> tuple[UsageExport, ...]:
    """The consumer's minted, unacknowledged deltas in mint order, each joined to its ledger row's
    immutable descriptive fields. A delivery the consumer never saw acknowledged stays pending and
    re-reads identically — the frozen intent, never a recomputation."""
    export = tables.ledger_export
    rows = (
        await connection.execute(
            sa.select(
                export.c.ledger_id,
                export.c.from_amount,
                (export.c.to_amount - export.c.from_amount).label("amount"),
                (export.c.to_micro_usd - export.c.from_micro_usd).label("priced_micro_usd"),
                tables.ledger.c.dimension,
                tables.ledger.c.model,
                tables.ledger.c.price_digest,
                tables.ledger.c.turn_id,
                export.c.byok,
                export.c.occurred_at,
            )
            .select_from(export.join(tables.ledger, tables.ledger.c.id == export.c.ledger_id))
            .where(
                export.c.consumer == consumer,
                export.c.workspace_id == workspace_id,
                export.c.acked_at.is_(None),
            )
            .order_by(export.c.created_at, export.c.ledger_id, export.c.from_amount)
            .limit(limit)
        )
    ).all()
    return tuple(
        UsageExport(
            ledger_id=row.ledger_id,
            from_amount=row.from_amount,
            amount=row.amount,
            priced_micro_usd=row.priced_micro_usd,
            dimension=row.dimension,
            model=row.model,
            price_digest=row.price_digest,
            turn_id=row.turn_id,
            byok=row.byok,
            occurred_at=row.occurred_at,
        )
        for row in rows
    )


async def ack_usage_exports(
    connection: AsyncConnection,
    workspace_id: UUID,
    consumer: str,
    exports: tuple[UsageExport, ...],
) -> None:
    """Mark delivered intents acknowledged so they leave the pending read. Only ever called after
    the external API accepted the batch; a crash before this lands re-delivers, and the frozen
    intent plus the receiver's dedup make the re-delivery a no-op."""
    keys = sa.or_(
        *(
            (tables.ledger_export.c.ledger_id == export.ledger_id)
            & (tables.ledger_export.c.from_amount == export.from_amount)
            for export in exports
        )
    )
    await connection.execute(
        sa.update(tables.ledger_export)
        .where(
            tables.ledger_export.c.consumer == consumer,
            tables.ledger_export.c.workspace_id == workspace_id,
            keys,
        )
        .values(acked_at=sa.func.now(), updated_at=sa.func.now())
    )


def metered_workspaces() -> WorkspaceCandidates:
    """Candidates for a usage-export job: every workspace that has ever metered. Coarse on purpose
    — the per-tick no-op for a fully exported workspace is one indexed pending read."""
    return owner_candidates(lambda: sa.select(tables.ledger.c.workspace_id).distinct())


@dataclass(frozen=True, slots=True)
class SpendCap:
    """One workspace spend limit: a scope (the whole workspace, one member, or one agent), the
    subject it binds (the member/agent id, or None for the workspace), a rolling window in seconds,
    a limit in micro-USD, and what a breach does — park (hold, resume when raised) or reject."""

    scope: CapScope
    subject_id: UUID | None
    window_seconds: int
    limit_micro_usd: int
    on_breach: OnBreach


@dataclass(frozen=True, slots=True)
class SpendDecision:
    outcome: SpendOutcome
    message: str


@dataclass(frozen=True)
class SpendEvaluator:
    """Decide whether a turn may run under the workspace's caps: read every cap that applies to this
    turn's workspace, member, and agent, sum the priced ledger over each cap's rolling window, and
    return allow / park / reject. Every applicable cap must have headroom (the tightest binds); a
    breach parks unless any breached cap rejects, in which case reject wins. `decide` is the whole
    workflow, its `_` steps beneath it in execution order; the caller supplies the connection so the
    same decision runs inside an admission transaction or a fresh read at a mid-turn step."""

    workspace_id: UUID
    member_id: UUID | None
    agent_id: UUID

    async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision:
        caps = await self._applicable_caps(connection)
        key = (self.workspace_id, self.member_id, self.agent_id)
        if not caps:
            _note_absent_caps(key)
            return SpendDecision(outcome=ALLOW, message="")
        _no_applicable_caps.pop(key, None)
        breaches = [
            cap
            for cap in caps
            if await self._used_micro_usd(connection, cap) + pending_micro_usd > cap.limit_micro_usd
        ]
        if not breaches:
            return SpendDecision(outcome=ALLOW, message="")
        outcome: SpendOutcome = REJECT if any(cap.on_breach == REJECT for cap in breaches) else PARK
        return SpendDecision(outcome=outcome, message=self._message(outcome, breaches))

    async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]:
        rows = await connection.execute(
            sa.select(
                tables.spend_cap.c.scope,
                tables.spend_cap.c.subject_id,
                tables.spend_cap.c.window_seconds,
                tables.spend_cap.c.limit_micro_usd,
                tables.spend_cap.c.on_breach,
            ).where(
                tables.spend_cap.c.workspace_id == self.workspace_id,
                sa.or_(
                    tables.spend_cap.c.scope == WORKSPACE_SCOPE,
                    (tables.spend_cap.c.scope == MEMBER_SCOPE)
                    & (tables.spend_cap.c.subject_id == self.member_id),
                    (tables.spend_cap.c.scope == AGENT_SCOPE)
                    & (tables.spend_cap.c.subject_id == self.agent_id),
                ),
            )
        )
        return tuple(
            SpendCap(
                scope=row.scope,
                subject_id=row.subject_id,
                window_seconds=row.window_seconds,
                limit_micro_usd=int(row.limit_micro_usd),
                on_breach=row.on_breach,
            )
            for row in rows
        )

    async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=cap.window_seconds)
        summed = sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0)
        window = tables.ledger.c.created_at >= cutoff
        match cap.scope:
            case "workspace":
                query = sa.select(summed).where(
                    tables.ledger.c.workspace_id == self.workspace_id, window
                )
            case "member":
                query = (
                    sa.select(summed)
                    .select_from(
                        tables.ledger.join(tables.turn).join(
                            tables.conversation,
                            tables.turn.c.conversation_id == tables.conversation.c.id,
                        )
                    )
                    .where(tables.conversation.c.member_id == cap.subject_id, window)
                )
            case "agent":
                query = (
                    sa.select(summed)
                    .select_from(tables.ledger.join(tables.turn))
                    .where(tables.turn.c.agent_id == cap.subject_id, window)
                )
        return int((await connection.execute(query)).scalar_one())

    def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str:
        tightest = min(breaches, key=lambda cap: cap.limit_micro_usd)
        dollars = tightest.limit_micro_usd / MICRO_USD_PER_USD
        if outcome == REJECT:
            return (
                f"This turn was declined: the {tightest.scope} spend cap of "
                f"${dollars:,.2f} is reached."
            )
        return (
            f"This turn is parked: the {tightest.scope} spend cap of ${dollars:,.2f} is reached. "
            "It resumes when the cap is raised."
        )


@dataclass(frozen=True, slots=True)
class DimensionTotal:
    dimension: str
    amount: int
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class SubjectTotal:
    subject_id: UUID
    label: str
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class PriceDigestTotal:
    price_digest: str
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class SpendReport:
    """A window's ledger, summed four ways: the workspace total, per member, and per agent, plus
    the per-dimension split so a reader sees priced tokens beside the egress request count, and the
    per-price-digest split so an audit attributes each burn to the rate version that priced it —
    the reconciliation seam over a window that spans a price-table change."""

    window_seconds: int
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_member: tuple[SubjectTotal, ...]
    by_agent: tuple[SubjectTotal, ...]
    by_price_digest: tuple[PriceDigestTotal, ...]


@dataclass(frozen=True, slots=True)
class SpendCapLine:
    window_seconds: int
    limit_micro_usd: int
    on_breach: OnBreach


@dataclass(frozen=True, slots=True)
class AgentSpendReport:
    """One agent's slice of the same window: the total and per-dimension sums of ledger rows whose
    turns ran as this agent, beside the agent-scoped caps that bound it — the member-visible
    per-agent view, distinct from the workspace-wide `SpendReport` an admin reads."""

    window_seconds: int
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    caps: tuple[SpendCapLine, ...]


@dataclass(frozen=True, slots=True)
class MemberSpendReport:
    """One member's own slice of the same window: the total and per-dimension sums of ledger rows
    whose turns ran in that member's conversations, beside the member-scoped caps that bound them —
    what a member may read about their own burn, naming no other member and no agent."""

    window_seconds: int
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    caps: tuple[SpendCapLine, ...]


@dataclass(frozen=True)
class SpendRollup:
    """Sum the workspace's ledger over a rolling window for the `ufoctl spend` CLI and the web
    view. `read` is the whole workflow: the window total, then the per-dimension, per-member,
    per-agent, and per-price-digest breakdowns — each a grouped sum the caller renders. Member and
    agent rows join through the turn, so a turn with no member (a subagent conversation) drops out
    of the member breakdown while still counting in the workspace total. The price-digest breakdown
    covers only priced rows (egress rows carry no digest), so it attributes token spend to each rate
    version present in the window."""

    workspace_id: UUID

    async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport:
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        window = (tables.ledger.c.workspace_id == self.workspace_id) & (
            tables.ledger.c.created_at >= cutoff
        )
        total = int(
            (
                await connection.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0)
                    ).where(window)
                )
            ).scalar_one()
        )
        by_dimension = tuple(
            DimensionTotal(row.dimension, int(row.amount), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    sa.func.sum(tables.ledger.c.amount).label("amount"),
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .where(window)
                .group_by(tables.ledger.c.dimension)
                .order_by(tables.ledger.c.dimension)
            )
        )
        by_member = tuple(
            SubjectTotal(row.member_id, row.email, int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.conversation.c.member_id,
                    tables.member.c.email,
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .select_from(
                    tables.ledger.join(tables.turn)
                    .join(
                        tables.conversation,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                    .join(tables.member, tables.conversation.c.member_id == tables.member.c.id)
                )
                .where(window)
                .group_by(tables.conversation.c.member_id, tables.member.c.email)
                .order_by(tables.member.c.email)
            )
        )
        by_agent = tuple(
            SubjectTotal(row.agent_id, row.name, int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.turn.c.agent_id,
                    tables.agent.c.name,
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .select_from(tables.ledger.join(tables.turn).join(tables.agent))
                .where(window)
                .group_by(tables.turn.c.agent_id, tables.agent.c.name)
                .order_by(tables.agent.c.name)
            )
        )
        by_price_digest = tuple(
            PriceDigestTotal(row.price_digest, int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.ledger.c.price_digest,
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .where(window & tables.ledger.c.price_digest.isnot(None))
                .group_by(tables.ledger.c.price_digest)
                .order_by(tables.ledger.c.price_digest)
            )
        )
        return SpendReport(
            window_seconds, total, by_dimension, by_member, by_agent, by_price_digest
        )

    async def read_agent(
        self, connection: AsyncConnection, agent_id: UUID, window_seconds: int
    ) -> AgentSpendReport:
        """One agent's rolling-window spend plus its agent-scoped caps. Ledger rows reach an agent
        through their turn, so a turn-less row (`record_workspace_usage` writes them) drops out of
        the agent view rather than misattribute — it still counts in the workspace rollup."""
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        window = (
            (tables.ledger.c.workspace_id == self.workspace_id)
            & (tables.ledger.c.created_at >= cutoff)
            & (tables.turn.c.agent_id == agent_id)
        )
        joined = tables.ledger.join(tables.turn)
        by_dimension = tuple(
            DimensionTotal(row.dimension, int(row.amount), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    sa.func.sum(tables.ledger.c.amount).label("amount"),
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .select_from(joined)
                .where(window)
                .group_by(tables.ledger.c.dimension)
                .order_by(tables.ledger.c.dimension)
            )
        )
        caps = tuple(
            SpendCapLine(int(row.window_seconds), int(row.limit_micro_usd), row.on_breach)
            for row in await connection.execute(
                sa.select(
                    tables.spend_cap.c.window_seconds,
                    tables.spend_cap.c.limit_micro_usd,
                    tables.spend_cap.c.on_breach,
                )
                .where(
                    tables.spend_cap.c.workspace_id == self.workspace_id,
                    tables.spend_cap.c.scope == AGENT_SCOPE,
                    tables.spend_cap.c.subject_id == agent_id,
                )
                .order_by(tables.spend_cap.c.window_seconds)
            )
        )
        return AgentSpendReport(
            window_seconds,
            sum(line.priced_micro_usd for line in by_dimension),
            by_dimension,
            caps,
        )

    async def read_member(
        self, connection: AsyncConnection, member_id: UUID, window_seconds: int
    ) -> MemberSpendReport:
        """One member's own rolling-window spend plus their member-scoped caps. A ledger row reaches
        a member through its turn's conversation — the same join `SpendEvaluator` sums a member cap
        over and `read`'s per-member breakdown groups by — so a member's own number here and the
        number their cap binds on are one truth. A turn-less or memberless row (a subagent
        conversation's) drops out rather than misattribute; it still counts in the workspace
        rollup."""
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        joined = tables.ledger.join(tables.turn).join(
            tables.conversation, tables.turn.c.conversation_id == tables.conversation.c.id
        )
        window = (
            (tables.ledger.c.workspace_id == self.workspace_id)
            & (tables.ledger.c.created_at >= cutoff)
            & (tables.conversation.c.member_id == member_id)
        )
        by_dimension = tuple(
            DimensionTotal(row.dimension, int(row.amount), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    sa.func.sum(tables.ledger.c.amount).label("amount"),
                    sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                )
                .select_from(joined)
                .where(window)
                .group_by(tables.ledger.c.dimension)
                .order_by(tables.ledger.c.dimension)
            )
        )
        caps = tuple(
            SpendCapLine(int(row.window_seconds), int(row.limit_micro_usd), row.on_breach)
            for row in await connection.execute(
                sa.select(
                    tables.spend_cap.c.window_seconds,
                    tables.spend_cap.c.limit_micro_usd,
                    tables.spend_cap.c.on_breach,
                )
                .where(
                    tables.spend_cap.c.workspace_id == self.workspace_id,
                    tables.spend_cap.c.scope == MEMBER_SCOPE,
                    tables.spend_cap.c.subject_id == member_id,
                )
                .order_by(tables.spend_cap.c.window_seconds)
            )
        )
        return MemberSpendReport(
            window_seconds,
            sum(line.priced_micro_usd for line in by_dimension),
            by_dimension,
            caps,
        )
