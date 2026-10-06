"""Token pricing, the ledger's writes, and the spend caps decided against the ledger."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.models.pricing import MICRO_USD_PER_USD, Pricing
from ufo.harness.o11y import warn
from ufo.runtime.billing.spend import (
    ALLOWED,
    PARK,
    REJECT,
    Charge,
    SpendDecision,
    SpendGate,
    SpendOutcome,
)
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.schema import tables
from ufo.schema.records import TurnStatus, Usage, ledger_id_for

TOKENS_DIMENSION = "tokens"
EGRESS_DIMENSION = "egress"
SANDBOX_TOKENS_DIMENSION = "sandbox_tokens"
IMAGES_DIMENSION = "images"
VIDEOS_DIMENSION = "videos"
UNCACHED_PROMPT_WARN_TOKENS = 20_000
"""Where a turn that cached nothing stops being a small cold prompt and starts being a fault. Well
past every supported provider's minimum cacheable prefix, the largest of which is 2,048."""

CapScope = Literal["workspace", "member", "agent"]
WORKSPACE_SCOPE: CapScope = "workspace"
MEMBER_SCOPE: CapScope = "member"
AGENT_SCOPE: CapScope = "agent"

OnBreach = Literal["park", "reject"]

QUEUED: TurnStatus = "queued"

CAP_PRESENCE_TTL_SECONDS = 5.0
CAP_PRESENCE_CACHE_MAX = 4096
TURN_MEMBER_ID = tables.turn.c.speaker_member_id
"""The member a turn's spend is attributed to: its bound speaker, if it has one."""
_no_applicable_caps: dict[tuple[UUID, UUID | None, UUID | None], float] = {}


class TurnUsageConflict(RuntimeError):
    """One attempt presented cumulative usage that cannot safely advance its ledger row."""


class OffTurnSpendRefused(RuntimeError):
    """An off-turn model call was refused by its workspace spend gates. `model` names the model the
    gates refused, because a refusal holds per model rather than per workspace: a gate may exempt a
    model the workspace's own key pays for, so one off-turn model can be refused while another is
    allowed for the whole time the hold stands."""

    def __init__(self, outcome: SpendOutcome, message: str, model: str) -> None:
        self.outcome = outcome
        self.model = model
        super().__init__(message)


def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool:
    """Connectionless fast-path: True only when a recent decision found no cap applies to this
    (workspace, member, agent), within a short TTL. The per-round enforcement then skips its DB
    round-trip — the common no-caps deploy pays nothing per round. Keyed by the exact triple so a
    cap on another member never suppresses this one; a newly-set cap takes effect within the TTL."""
    expiry = _no_applicable_caps.get((workspace_id, member_id, agent_id))
    return expiry is not None and expiry > time.monotonic()


def _note_absent_caps(key: tuple[UUID, UUID | None, UUID | None]) -> None:
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
        + usage.cache_write_5m_tokens
        + usage.cache_write_30m_tokens
        + usage.cache_write_1h_tokens
    )


def _prompt_tokens(usage: Usage) -> int:
    """The tokens the provider read to answer, cached or not — the denominator of the cache share a
    terminal frame renders."""
    return (
        usage.input_tokens
        + usage.cache_read_tokens
        + usage.cache_write_5m_tokens
        + usage.cache_write_30m_tokens
        + usage.cache_write_1h_tokens
    )


def _warn_uncached(model: str, usage: Usage, pricing: Pricing) -> None:
    """`anthropic/claude-fable-5.1` on OpenRouter never cached and paid full input rate: 4.3M input
    tokens and $45 in one turn, which only the bill showed."""
    price = pricing.prices.get(model)
    if price is None or price.cache_read == 0:
        return
    if usage.cache_read_tokens or _prompt_tokens(usage) < UNCACHED_PROMPT_WARN_TOKENS:
        return
    if usage.cache_write_5m_tokens or usage.cache_write_30m_tokens or usage.cache_write_1h_tokens:
        return
    warn("accounting.uncached_prompt", model=model, prompt_tokens=_prompt_tokens(usage))


@dataclass(frozen=True)
class Ledger:
    """The ledger's priced writes, and the spend gates each one tells what it booked. Built once at
    boot with the deploy's gates; every writer calls `charged` on the gates once per positive priced
    delta it books, in its own transaction and after its replay guard, so a replayed snapshot
    charges nothing twice and a gate that raises takes the ledger row back with it."""

    gates: tuple[SpendGate, ...] = ()

    async def record_turn_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        model: str,
        usage: Usage,
        attempt: str = "",
        pricing: Pricing = CORE_PRICING,
        byok: bool = False,
    ) -> None:
        """One cumulative billing row per turn run attempt. A rolling shutdown bills the completed
        rounds before cancellation; DBOS recovery replays those same steps under the same attempt
        and may then complete more rounds, so a later snapshot advances that row to the larger
        cumulative usage and charges only its price delta. An equal or older replay prefix changes
        nothing. Two snapshots whose token-class totals cross are not one cumulative history and
        fail loud.

        A parked turn resumes under a fresh attempt (workflow id), so its new partial burn gets
        another row and turn cost sums both. The attempt id is therefore both the replay key and
        the boundary between additive runs: within it snapshots replace monotonically; across it
        rows add.

        The row carries the burn's prompt split beside its total, so a terminal frame's cache share
        is a read of the same row the tokens, cost and model come off rather than a second account
        of the same spend.

        `byok` is decided once, when the turn is set up and against the key that will serve it,
        rather than re-read here: credential rows can change while a turn runs, so a value read at
        terminal would make a turn free because a key arrived mid-run, or charge for one the
        workspace's own key paid because a key left. It is the same question the usage export
        freezes at mint, and it is what a charge's `platform_paid` carries."""
        total = _total_tokens(usage)
        if total == 0:
            return
        _warn_uncached(model, usage, pricing)
        ledger_id = ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, attempt)
        priced = pricing.micro_usd(model, usage)
        current = (
            (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.input_tokens,
                        tables.ledger.c.output_tokens,
                        tables.ledger.c.cache_read_tokens,
                        tables.ledger.c.cache_write_5m_tokens,
                        tables.ledger.c.cache_write_30m_tokens,
                        tables.ledger.c.cache_write_1h_tokens,
                        tables.ledger.c.byok,
                        tables.ledger.c.priced_micro_usd,
                        tables.ledger.c.model,
                        tables.ledger.c.price_digest,
                    )
                    .where(tables.ledger.c.id == ledger_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is not None:
            if (
                current["model"] != model
                or current["price_digest"] != pricing.digest
                or current["byok"] != byok
            ):
                raise TurnUsageConflict("one turn attempt changed its billing identity")
            previous = (
                int(current["input_tokens"]),
                int(current["output_tokens"]),
                int(current["cache_read_tokens"]),
                int(current["cache_write_5m_tokens"]),
                int(current["cache_write_30m_tokens"]),
                int(current["cache_write_1h_tokens"]),
            )
            incoming = (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_5m_tokens,
                usage.cache_write_30m_tokens,
                usage.cache_write_1h_tokens,
            )
            if all(new <= old for new, old in zip(incoming, previous, strict=True)):
                return
            if not all(new >= old for new, old in zip(incoming, previous, strict=True)):
                raise TurnUsageConflict("one turn attempt reported incomparable usage snapshots")
            priced_delta = priced - int(current["priced_micro_usd"])
            if priced_delta < 0:
                raise TurnUsageConflict("one turn attempt's cumulative price decreased")
            await connection.execute(
                sa.update(tables.ledger)
                .where(tables.ledger.c.id == ledger_id)
                .values(
                    amount=total,
                    prompt_tokens=_prompt_tokens(usage),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_5m_tokens=usage.cache_write_5m_tokens,
                    cache_write_30m_tokens=usage.cache_write_30m_tokens,
                    cache_write_1h_tokens=usage.cache_write_1h_tokens,
                    priced_micro_usd=priced,
                    updated_at=sa.func.now(),
                )
            )
            await self._charged(
                connection,
                ledger_id,
                workspace_id,
                turn_id,
                TOKENS_DIMENSION,
                priced_delta,
                not byok,
            )
            return
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=ledger_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension=TOKENS_DIMENSION,
                amount=total,
                prompt_tokens=_prompt_tokens(usage),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_5m_tokens=usage.cache_write_5m_tokens,
                cache_write_30m_tokens=usage.cache_write_30m_tokens,
                cache_write_1h_tokens=usage.cache_write_1h_tokens,
                byok=byok,
                token_classes_complete=True,
                priced_micro_usd=priced,
                model=model,
                price_digest=pricing.digest,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await self._charged(
            connection, ledger_id, workspace_id, turn_id, TOKENS_DIMENSION, priced, not byok
        )

    async def record_workspace_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        model: str,
        usage: Usage,
        pricing: Pricing = CORE_PRICING,
        byok: bool = False,
        call_id: UUID | None = None,
    ) -> None:
        """Bill a background job's metered model call to the workspace, not a turn: one priced
        `tokens` row with `turn_id` NULL, stamped with the model and price digest exactly as a
        turn's tokens are. A supplied call ID deduplicates an identical write and rejects changed
        usage; otherwise the row gets a fresh ID. Each genuine completion is billed once — there is
        no DBOS step checkpoint around a job's model call, so a workflow replay re-invokes the
        provider (a real charge) and bills that invocation, never a phantom double or a lost burn.
        It lands in the workspace spend total and every workspace-scoped cap window (which sum by
        `workspace_id`), and is excluded from per-member and per-agent attribution (which join
        through `turn` — a NULL FK drops out), because a job's spend belongs to no member or agent.

        It bills against the serving model's own rate: no agent authored this call, so there is no
        delegated choice to charge for and nothing to route. A job served by the workspace's own
        key charges `platform_paid` false, exactly as a turn does."""
        total = _total_tokens(usage)
        if total == 0:
            return
        priced = pricing.micro_usd(model, usage)
        if call_id is not None:
            existing = (
                (
                    await connection.execute(
                        sa.select(
                            tables.ledger.c.workspace_id,
                            tables.ledger.c.turn_id,
                            tables.ledger.c.dimension,
                            tables.ledger.c.model,
                            tables.ledger.c.price_digest,
                            tables.ledger.c.byok,
                            tables.ledger.c.priced_micro_usd,
                            tables.ledger.c.input_tokens,
                            tables.ledger.c.output_tokens,
                            tables.ledger.c.cache_read_tokens,
                            tables.ledger.c.cache_write_5m_tokens,
                            tables.ledger.c.cache_write_30m_tokens,
                            tables.ledger.c.cache_write_1h_tokens,
                        ).where(tables.ledger.c.id == call_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                expected = {
                    "workspace_id": workspace_id,
                    "turn_id": None,
                    "dimension": TOKENS_DIMENSION,
                    "model": model,
                    "price_digest": pricing.digest,
                    "byok": byok,
                    "priced_micro_usd": priced,
                    **usage.model_dump(),
                }
                if any(existing[key] != value for key, value in expected.items()):
                    raise TurnUsageConflict(
                        "one provider call changed its billing identity or usage"
                    )
                return
        ledger_id = call_id or uuid4()
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=ledger_id,
                workspace_id=workspace_id,
                turn_id=None,
                dimension=TOKENS_DIMENSION,
                amount=total,
                prompt_tokens=_prompt_tokens(usage),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_5m_tokens=usage.cache_write_5m_tokens,
                cache_write_30m_tokens=usage.cache_write_30m_tokens,
                cache_write_1h_tokens=usage.cache_write_1h_tokens,
                byok=byok,
                token_classes_complete=True,
                priced_micro_usd=priced,
                model=model,
                price_digest=pricing.digest,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await self._charged(
            connection, ledger_id, workspace_id, None, TOKENS_DIMENSION, priced, not byok
        )

    async def record_image_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        model: str,
        images: int,
        micro_usd: int,
    ) -> None:
        """Meter generated images as an `images` ledger row per turn: `amount` counts the images and
        `priced_micro_usd` is what they cost, both accumulated atomically so several generations on
        one turn never lose one. The caller prices them, because an image model is not a `ModelSpec`
        and its unit is an image (or a megapixel, or an output image token) rather than the four
        token counters `ModelPrice` rates — the provider extension reads the charge off its own
        response and books it here, and the row carries no `price_digest` because no pinned rate
        table describes it. Keyed with an empty attempt under a dimension distinct from `tokens`, so
        its id can never collide with the row `Ledger.record_turn_usage` writes for the same turn,
        and a parked-then-resumed turn keeps accumulating into the one row."""
        await self._record_media_usage(
            connection, workspace_id, turn_id, IMAGES_DIMENSION, model, images, micro_usd
        )

    async def record_video_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        model: str,
        videos: int,
        micro_usd: int,
    ) -> None:
        """Meter generated videos as a `videos` ledger row per turn, on the same terms as
        `Ledger.record_image_usage`: `amount` counts the videos, `priced_micro_usd` is what the
        provider charged for them, and the caller prices the call because a video model is not a
        `ModelSpec` and its unit is an output second rather than a token."""
        await self._record_media_usage(
            connection, workspace_id, turn_id, VIDEOS_DIMENSION, model, videos, micro_usd
        )

    async def _record_media_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        dimension: str,
        model: str,
        amount: int,
        micro_usd: int,
    ) -> None:
        """`egress` is the one metered dimension that never charges: it counts requests and prices
        at zero."""
        ledger_id = ledger_id_for(workspace_id, turn_id, dimension)
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.ledger)
            .values(
                id=ledger_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension=dimension,
                amount=amount,
                priced_micro_usd=micro_usd,
                model=model,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=[tables.ledger.c.id],
                set_={
                    "amount": tables.ledger.c.amount + amount,
                    "priced_micro_usd": tables.ledger.c.priced_micro_usd + micro_usd,
                    "updated_at": sa.func.now(),
                },
            )
        )
        await self._charged(
            connection, ledger_id, workspace_id, turn_id, dimension, micro_usd, True
        )

    async def _charged(
        self,
        connection: AsyncConnection,
        ledger_id: UUID,
        workspace_id: UUID,
        turn_id: UUID | None,
        dimension: str,
        delta_micro_usd: int,
        platform_paid: bool,
    ) -> None:
        if delta_micro_usd <= 0:
            return
        charge = Charge(
            ledger_id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=dimension,
            delta_micro_usd=delta_micro_usd,
            platform_paid=platform_paid,
        )
        for gate in self.gates:
            await gate.charged(connection, charge)


UNGATED_LEDGER = Ledger()


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
    export seam's settlement knowledge, kept beside the writers that define it: a `tokens` row
    normally lands once at terminal, but a cancelled workflow may write a partial cumulative
    snapshot that recovery later advances, so any growth mints a further intent from the prior
    high-water mark. A `sandbox_tokens`, `images` or `videos` row accumulates until its turn is
    terminal, and `EXPORT_SETTLE_MARGIN_SECONDS` past `turn.updated_at` only bounds how often a late
    egress-proxy write costs an extra top-up intent. No growth is lost to timing.
    Egress rows (a zero-priced request count) never export. Usage settling before `floor` never
    mints — the consumer's backfill bound. Idempotent: an intent's `(consumer, ledger_id,
    from_amount)` key makes concurrent or replayed mints collapse onto one frozen row.

    Each intent copies the `byok` value the ledger writer froze when the provider attempt began.
    Credential changes after that attempt cannot change who paid for it."""
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
    latest_amount = sa.func.max(tables.ledger_export.c.to_amount)
    latest_micro_usd = sa.func.max(tables.ledger_export.c.to_micro_usd)
    growth = (
        await connection.execute(
            sa.select(
                tables.ledger.c.id,
                tables.ledger.c.workspace_id,
                tables.ledger.c.amount,
                tables.ledger.c.priced_micro_usd,
                tables.ledger.c.dimension,
                tables.ledger.c.model,
                tables.ledger.c.byok,
                tables.ledger.c.token_classes_complete,
                tables.turn.c.byok.label("turn_byok"),
                tables.ledger.c.updated_at,
                sa.func.coalesce(latest_amount, 0).label("from_amount"),
                sa.func.coalesce(latest_micro_usd, 0).label("from_micro_usd"),
            )
            .select_from(
                tables.ledger.outerjoin(
                    tables.turn, tables.turn.c.id == tables.ledger.c.turn_id
                ).outerjoin(
                    tables.ledger_export,
                    (tables.ledger_export.c.consumer == consumer)
                    & (tables.ledger_export.c.ledger_id == tables.ledger.c.id),
                )
            )
            .where(
                tables.ledger.c.workspace_id == workspace_id,
                sa.or_(
                    (tables.ledger.c.dimension == TOKENS_DIMENSION)
                    & (tables.ledger.c.created_at >= floor),
                    tables.ledger.c.dimension.in_(
                        (SANDBOX_TOKENS_DIMENSION, IMAGES_DIMENSION, VIDEOS_DIMENSION)
                    )
                    & tables.turn.c.terminal.isnot(None)
                    & (tables.turn.c.updated_at <= settle_cutoff)
                    & (tables.turn.c.updated_at >= floor),
                ),
            )
            .group_by(
                tables.ledger.c.id,
                tables.ledger.c.workspace_id,
                tables.ledger.c.amount,
                tables.ledger.c.priced_micro_usd,
                tables.ledger.c.dimension,
                tables.ledger.c.model,
                tables.ledger.c.byok,
                tables.ledger.c.token_classes_complete,
                tables.turn.c.byok,
                tables.ledger.c.updated_at,
            )
            .having(sa.or_(latest_amount.is_(None), tables.ledger.c.amount > latest_amount))
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
                    False
                    if row.dimension != TOKENS_DIMENSION
                    else (
                        row.byok
                        if row.token_classes_complete and row.byok is not None
                        else (
                            row.turn_byok
                            if row.turn_byok is not None
                            else (key_slot_for(row.model) in stored_slots)
                        )
                    )
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


@dataclass(frozen=True)
class SpendEvaluator:
    """Decide whether work may run under the workspace's caps: every cap that applies to its
    workspace and any attributed member and agent is read, the priced ledger summed over each
    cap's rolling window, and the answer is allow / park / reject. Every applicable cap must have
    headroom (the tightest binds); a breach parks unless any breached cap rejects, in which case
    reject wins.
    `decide` is the whole workflow, its `_` steps beneath it in execution order; the caller supplies
    the connection so the same decision runs inside an admission transaction or a fresh read at a
    mid-turn step."""

    workspace_id: UUID
    member_id: UUID | None
    agent_id: UUID | None

    async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision:
        caps = await self._applicable_caps(connection)
        key = (self.workspace_id, self.member_id, self.agent_id)
        if not caps:
            _note_absent_caps(key)
            return ALLOWED
        _no_applicable_caps.pop(key, None)
        breaches = [
            cap
            for cap in caps
            if await self._used_micro_usd(connection, cap) + pending_micro_usd > cap.limit_micro_usd
        ]
        if not breaches:
            return ALLOWED
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
                    .select_from(tables.ledger.join(tables.turn))
                    .where(TURN_MEMBER_ID == cap.subject_id, window)
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
class ServiceTotal:
    service: str
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class SubjectTotal:
    subject_id: UUID | None
    label: str
    tokens: int
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class PriceDigestTotal:
    price_digest: str
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class OriginTotal:
    """Spend gathered under the place a member started it — the surface's own name for the origin
    (`surface_label`, a Slack channel's `#eng`) where the surface names one, else the surface
    itself, because a `queue_key` is a wire identifier no member reads."""

    surface: str | None
    label: str
    tokens: int
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class UsageTotal:
    tokens: int
    token_micro_usd: int
    total_micro_usd: int


@dataclass(frozen=True, slots=True)
class DailyUsageTotal:
    day: str
    tokens: int
    token_micro_usd: int
    total_micro_usd: int


@dataclass(frozen=True, slots=True)
class UsageBreakdown:
    label: str
    tokens: int
    priced_micro_usd: int


@dataclass(frozen=True, slots=True)
class UsageDetails:
    selected: UsageTotal
    all_time: UsageTotal
    first_used_at: datetime | None
    previous_tokens: int | None
    daily: tuple[DailyUsageTotal, ...]
    by_execution: tuple[UsageBreakdown, ...]
    by_model: tuple[UsageBreakdown, ...]


@dataclass(frozen=True, slots=True)
class _LedgerRollup:
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_price_digest: tuple[PriceDigestTotal, ...]
    usage: UsageDetails

    @property
    def by_service(self) -> tuple[ServiceTotal, ...]:
        totals: dict[str, int] = {}
        for line in self.by_dimension:
            service = SERVICE_OF_DIMENSION[line.dimension]
            totals[service] = totals.get(service, 0) + line.priced_micro_usd
        return tuple(
            sorted(
                (ServiceTotal(service, priced) for service, priced in totals.items()),
                key=lambda total: (-total.priced_micro_usd, total.service),
            )
        )


@dataclass(frozen=True, slots=True)
class SpendReport:
    """A selected range and all-time workspace ledger, with daily, execution, model, dimension,
    service, member, agent, origin, and price-table totals."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_service: tuple[ServiceTotal, ...]
    by_member: tuple[SubjectTotal, ...]
    by_agent: tuple[SubjectTotal, ...]
    by_origin: tuple[OriginTotal, ...]
    by_price_digest: tuple[PriceDigestTotal, ...]
    usage: UsageDetails


@dataclass(frozen=True, slots=True)
class SpendTotals:
    """A selected range and all-time workspace ledger, with daily, execution, model, dimension,
    and service totals, naming no member or agent."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_service: tuple[ServiceTotal, ...]
    usage: UsageDetails


@dataclass(frozen=True, slots=True)
class SpendCapLine:
    window_seconds: int
    limit_micro_usd: int
    on_breach: OnBreach


@dataclass(frozen=True, slots=True)
class MemberSpendReport:
    """One member's selected range and all-time ledger, naming no other member or agent."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    caps: tuple[SpendCapLine, ...]
    usage: UsageDetails


TOKEN_DIMENSIONS = (TOKENS_DIMENSION, SANDBOX_TOKENS_DIMENSION)
SERVICE_OF_DIMENSION: Mapping[str, str] = {
    TOKENS_DIMENSION: "models",
    SANDBOX_TOKENS_DIMENSION: "models",
    IMAGES_DIMENSION: "models",
    VIDEOS_DIMENSION: "models",
    EGRESS_DIMENSION: "proxy",
}
WORKSPACE_JOB_LABEL = "Workspace jobs"
SELECTED_PERIOD = "selected"
PREVIOUS_PERIOD = "previous"


def _token_sum() -> sa.ColumnElement[int]:
    return sa.func.coalesce(
        sa.func.sum(
            sa.case(
                (tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS), tables.ledger.c.amount), else_=0
            )
        ),
        0,
    )


def _token_cost_sum() -> sa.ColumnElement[int]:
    return sa.func.coalesce(
        sa.func.sum(
            sa.case(
                (
                    tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS),
                    tables.ledger.c.priced_micro_usd,
                ),
                else_=0,
            )
        ),
        0,
    )


JOB_DAY_ROLLUP_JOB = "job_day_rollup"
JOB_DAY_ROLLUP_SCHEDULE = "0 30 0 * * *"
JOB_DAY_SETTLE_SECONDS = 900


def _earliest(held: datetime | None, offered: datetime | None) -> datetime | None:
    if offered is None:
        return held
    return offered if held is None or offered < held else held


def _settled(now: datetime) -> date:
    """Postgres `now()` is the transaction's start, so a transaction begun before midnight can
    commit a row onto yesterday; the margin holds a day open until it committed or died."""
    return (now - timedelta(seconds=JOB_DAY_SETTLE_SECONDS)).date()


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _previous_start(cutoff: datetime | None, now: datetime) -> datetime | None:
    return None if cutoff is None else cutoff - (now - cutoff)


async def _rolled_through(connection: AsyncConnection, workspace_id: UUID) -> date | None:
    """The writer rolls closed days oldest first."""
    rolled = (
        await connection.execute(
            sa.select(sa.func.max(tables.ledger_job_day.c.day)).where(
                tables.ledger_job_day.c.workspace_id == workspace_id
            )
        )
    ).scalar()
    return None if rolled is None else date.fromisoformat(str(rolled))


@dataclass(frozen=True)
class RolledDays:
    """The folded days a usage read stands on: the ledger predicate that skips what the fold
    already answers, and the folded rows that answer it instead. The two are built together
    because they must name the same span exactly once — a day counted in both is billed twice,
    and a day in neither vanishes."""

    skip: sa.ColumnElement[bool]
    rows: sa.Select[tuple[str, bool, object, str, str, str, str, int, int, datetime]]
    totals: sa.Select[tuple[int, int, int, datetime]]


def rolled_days(
    workspace_id: UUID,
    rolled_through: date,
    cutoff: datetime | None,
    previous_start: datetime | None,
) -> RolledDays:
    """Split a workspace's turn-less spend between the fold and the ledger for one read.

    Both halves are bounded by the same `rolled_through` the ledger predicate was built from, not
    by what the fold holds when each statement runs. The read takes a snapshot per statement, so a
    day the job commits between them would otherwise arrive in the fold while the ledger predicate
    still admits it, and be counted twice.

    Two days are always read from the ledger however far the fold has run: the day the range
    starts in and the day the selected period starts in. A folded day is whole, so a day a
    boundary cuts through cannot say which side of it its spend fell on, and counting the whole
    day would bill the part outside the range. Everything else on a folded day comes from the
    fold, and everything after it from the ledger."""
    folded = tables.ledger_job_day
    mine = folded.c.workspace_id == workspace_id
    partial = tuple({moment.date() for moment in (cutoff, previous_start) if moment is not None})
    skip: sa.ColumnElement[bool] = tables.ledger.c.turn_id.isnot(None) | (
        tables.ledger.c.created_at >= _day_start(rolled_through + timedelta(days=1))
    )
    for day in partial:
        skip |= (tables.ledger.c.created_at >= _day_start(day)) & (
            tables.ledger.c.created_at < _day_start(day + timedelta(days=1))
        )
    whole = mine & (folded.c.day <= rolled_through)
    if partial:
        whole &= folded.c.day.notin_(partial)
    token = folded.c.dimension.in_(TOKEN_DIMENSIONS)
    selected = sa.true() if cutoff is None else folded.c.day > cutoff.date()
    rows = sa.select(
        (
            sa.literal(SELECTED_PERIOD)
            if previous_start is None
            else sa.case((selected, SELECTED_PERIOD), else_=PREVIOUS_PERIOD)
        ).label("period"),
        token.label("token"),
        sa.case((selected, folded.c.day)).label("day"),
        sa.case((selected, folded.c.dimension)).label("dimension"),
        sa.case((selected & token, folded.c.model)).label("model"),
        sa.case((selected, folded.c.price_digest)).label("price_digest"),
        sa.cast(sa.null(), sa.Text).label("execution"),
        folded.c.amount.label("amount"),
        folded.c.priced_micro_usd.label("priced"),
        folded.c.first_used_at.label("first_used_at"),
    ).where(whole if previous_start is None else whole & (folded.c.day > previous_start.date()))
    totals = sa.select(
        sa.func.coalesce(sa.func.sum(sa.case((token, folded.c.amount), else_=0)), 0).label(
            "tokens"
        ),
        sa.func.coalesce(
            sa.func.sum(sa.case((token, folded.c.priced_micro_usd), else_=0)), 0
        ).label("token_cost"),
        sa.func.coalesce(sa.func.sum(folded.c.priced_micro_usd), 0).label("cost"),
        sa.func.min(folded.c.first_used_at).label("first_used_at"),
    ).where(whole)
    return RolledDays(skip=skip, rows=rows, totals=totals)


@dataclass(frozen=True)
class JobDayRollup:
    """Fold a workspace's turn-less ledger rows — a background job's model spend, which reaches no
    member, agent or conversation — into one row per closed day, dimension, model and price digest.

    Those rows are 90% of a busy workspace's ledger — 637,767 of the 730,257 in a 30-day window on
    2026-09-15 — and every usage read scanned all of them to reach totals no reader needs per
    call.

    A closed day is rewritten whole rather than merged into. `Ledger.record_workspace_usage` inserts
    a turn-less row and never updates it, so recomputing a day always yields the same sums and the
    delete-then-insert is idempotent under replay. Today stays in the ledger, because it is still
    being written."""

    workspace_id: UUID

    async def roll(self, connection: AsyncConnection, now: datetime) -> tuple[date, ...]:
        rolled_through = await _rolled_through(connection, self.workspace_id)
        days = await self._pending_days(connection, now, rolled_through)
        for day in days:
            await self._write_day(connection, day, now)
        return days

    async def _pending_days(
        self, connection: AsyncConnection, now: datetime, rolled_through: date | None
    ) -> tuple[date, ...]:
        scope = (
            (tables.ledger.c.workspace_id == self.workspace_id)
            & tables.ledger.c.turn_id.is_(None)
            & (tables.ledger.c.created_at < _day_start(_settled(now)))
        )
        if rolled_through is not None:
            scope &= tables.ledger.c.created_at >= _day_start(rolled_through + timedelta(days=1))
        day = sa.func.date(tables.ledger.c.created_at).label("day")
        rows = await connection.execute(sa.select(day).where(scope).distinct().order_by(day))
        return tuple(sorted(date.fromisoformat(str(row.day)) for row in rows))

    async def _write_day(self, connection: AsyncConnection, day: date, now: datetime) -> None:
        await connection.execute(
            sa.delete(tables.ledger_job_day).where(
                tables.ledger_job_day.c.workspace_id == self.workspace_id,
                tables.ledger_job_day.c.day == day,
            )
        )
        start = _day_start(day)
        totals = await connection.execute(
            sa.select(
                tables.ledger.c.dimension,
                tables.ledger.c.model,
                tables.ledger.c.price_digest,
                sa.func.sum(tables.ledger.c.amount).label("amount"),
                sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
                sa.func.min(tables.ledger.c.created_at).label("first_used_at"),
            )
            .where(
                tables.ledger.c.workspace_id == self.workspace_id,
                tables.ledger.c.turn_id.is_(None),
                tables.ledger.c.created_at >= start,
                tables.ledger.c.created_at < start + timedelta(days=1),
            )
            .group_by(
                tables.ledger.c.dimension, tables.ledger.c.model, tables.ledger.c.price_digest
            )
        )
        for row in totals:
            await connection.execute(
                sa.insert(tables.ledger_job_day).values(
                    id=uuid4(),
                    workspace_id=self.workspace_id,
                    day=day,
                    dimension=row.dimension,
                    model=row.model,
                    price_digest=row.price_digest,
                    amount=int(row.amount),
                    priced_micro_usd=int(row.priced),
                    first_used_at=row.first_used_at,
                    created_at=now,
                    updated_at=now,
                )
            )


def job_day_candidates() -> WorkspaceCandidates:
    """Every workspace, because naming only the ones with spend to fold costs more than folding.

    The ledger carries no index that answers "turn-less rows on a day this workspace has not
    folded": `date(created_at)` is not sargable and the turn-less rows are most of the table, so
    the question is a full scan of the one table this change exists to stop reading. A workspace
    with nothing to fold answers in an indexed range scan of its own rows instead, and the job
    fires once a day, because a day closes once a day."""
    return owner_candidates(lambda: sa.select(tables.workspace.c.id))


@dataclass(frozen=True)
class _AllTime:
    tokens: int
    token_cost: int
    cost: int
    first_used_at: datetime | None


async def _all_time(
    connection: AsyncConnection,
    source: sa.FromClause,
    scope: sa.ColumnElement[bool],
    rolled: RolledDays | None,
) -> _AllTime:
    """Disjoint: the scope's `rolled.skip` drops from the ledger read exactly the days
    `rolled.totals` sums."""
    totals = (
        await connection.execute(
            sa.select(
                _token_sum().label("tokens"),
                _token_cost_sum().label("token_cost"),
                sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0).label("cost"),
                sa.func.min(tables.ledger.c.created_at).label("first_used_at"),
            )
            .select_from(source)
            .where(scope)
        )
    ).one()
    held = _AllTime(
        int(totals.tokens), int(totals.token_cost), int(totals.cost), totals.first_used_at
    )
    if rolled is None:
        return held
    folded = (await connection.execute(rolled.totals)).one()
    return _AllTime(
        held.tokens + int(folded.tokens),
        held.token_cost + int(folded.token_cost),
        held.cost + int(folded.cost),
        _earliest(held.first_used_at, folded.first_used_at),
    )


async def _ledger_rollup(
    connection: AsyncConnection,
    source: sa.FromClause,
    scope: sa.ColumnElement[bool],
    cutoff: datetime | None,
    now: datetime,
    execution_column: sa.ColumnElement[str] | None,
    rolled: RolledDays | None = None,
) -> _LedgerRollup:
    created_at = tables.ledger.c.created_at
    selected = sa.true() if cutoff is None else created_at >= cutoff
    previous_start = _previous_start(cutoff, now)
    period = (
        sa.literal(SELECTED_PERIOD)
        if previous_start is None
        else sa.case(
            (selected, SELECTED_PERIOD),
            else_=PREVIOUS_PERIOD,
        )
    ).label("period")
    token = tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS).label("token")
    day = sa.case((selected, sa.func.date(created_at))).label("day")
    dimension = sa.case((selected, tables.ledger.c.dimension)).label("dimension")
    model = sa.case((selected & token, tables.ledger.c.model)).label("model")
    price_digest = sa.case((selected, tables.ledger.c.price_digest)).label("price_digest")
    execution = (
        sa.cast(sa.null(), sa.Text)
        if execution_column is None
        else sa.case(
            (
                selected & token & tables.ledger.c.turn_id.isnot(None),
                sa.func.coalesce(execution_column, ""),
            )
        )
    ).label("execution")
    all_time = _AllTime(0, 0, 0, None)
    detail_scope = scope
    if previous_start is not None:
        all_time = await _all_time(connection, source, scope, rolled)
        detail_scope &= created_at >= previous_start
    detail = (
        sa.select(
            period,
            token,
            day,
            dimension,
            model,
            price_digest,
            execution,
            sa.func.sum(tables.ledger.c.amount).label("amount"),
            sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
            sa.func.min(created_at).label("first_used_at"),
        )
        .select_from(source)
        .where(detail_scope)
        .group_by(period, token, day, dimension, model, price_digest, execution)
    )
    rows = await connection.execute(detail if rolled is None else detail.union_all(rolled.rows))
    all_time_tokens = all_time.tokens
    all_time_token_cost = all_time.token_cost
    all_time_cost = all_time.cost
    first_used_at = all_time.first_used_at
    selected_tokens = 0
    selected_token_cost = 0
    selected_cost = 0
    previous_tokens = 0
    dimensions: dict[str, tuple[int, int]] = {}
    digests: dict[str, int] = {}
    daily_totals: dict[date, tuple[int, int, int]] = {}
    executions: dict[str, tuple[int, int]] = {}
    models: dict[str, tuple[int, int]] = {}
    for row in rows:
        amount = int(row.amount)
        priced = int(row.priced)
        is_token = bool(row.token)
        all_time_cost += priced if cutoff is None else 0
        if is_token and cutoff is None:
            all_time_tokens += amount
            all_time_token_cost += priced
        used_at = row.first_used_at if cutoff is None else None
        if used_at is not None and (first_used_at is None or used_at < first_used_at):
            first_used_at = used_at
        if row.period == PREVIOUS_PERIOD:
            if is_token:
                previous_tokens += amount
            continue
        if row.period != SELECTED_PERIOD:
            continue
        selected_cost += priced
        if is_token:
            selected_tokens += amount
            selected_token_cost += priced
        if row.dimension is not None:
            prior_amount, prior_priced = dimensions.get(row.dimension, (0, 0))
            dimensions[row.dimension] = prior_amount + amount, prior_priced + priced
        if row.price_digest is not None:
            digests[row.price_digest] = digests.get(row.price_digest, 0) + priced
        current_day = date.fromisoformat(str(row.day))
        day_tokens, day_token_cost, day_cost = daily_totals.get(current_day, (0, 0, 0))
        daily_totals[current_day] = (
            day_tokens + (amount if is_token else 0),
            day_token_cost + (priced if is_token else 0),
            day_cost + priced,
        )
        if row.execution is not None:
            execution_tokens, execution_priced = executions.get(row.execution, (0, 0))
            executions[row.execution] = execution_tokens + amount, execution_priced + priced
        if row.model is not None:
            model_tokens, model_priced = models.get(row.model, (0, 0))
            models[row.model] = model_tokens + amount, model_priced + priced
    first_day = cutoff.date() if cutoff is not None else min(daily_totals, default=None)
    daily: tuple[DailyUsageTotal, ...] = ()
    if first_day is not None:
        days = (now.date() - first_day).days + 1
        daily = tuple(
            DailyUsageTotal(str(current), *daily_totals.get(current, (0, 0, 0)))
            for current in (first_day + timedelta(days=offset) for offset in range(days))
        )
    if first_used_at is not None and first_used_at.tzinfo is None:
        first_used_at = first_used_at.replace(tzinfo=UTC)
    return _LedgerRollup(
        total_micro_usd=selected_cost,
        by_dimension=tuple(
            DimensionTotal(name, amount, priced)
            for name, (amount, priced) in sorted(dimensions.items())
        ),
        by_price_digest=tuple(
            PriceDigestTotal(digest, priced) for digest, priced in sorted(digests.items())
        ),
        usage=UsageDetails(
            selected=UsageTotal(selected_tokens, selected_token_cost, selected_cost),
            all_time=UsageTotal(all_time_tokens, all_time_token_cost, all_time_cost),
            first_used_at=first_used_at,
            previous_tokens=None if cutoff is None else previous_tokens,
            daily=daily,
            by_execution=tuple(
                UsageBreakdown(name, tokens, priced)
                for name, (tokens, priced) in sorted(executions.items())
            ),
            by_model=tuple(
                UsageBreakdown(name, tokens, priced)
                for name, (tokens, priced) in sorted(models.items())
            ),
        ),
    )


@dataclass(frozen=True)
class SpendRollup:
    """Read workspace, member, and agent usage from the ledger. A range also returns its daily
    history and preceding range; `None` selects all time. Token totals contain `tokens` and
    `sandbox_tokens`. Other dimensions affect spend totals only."""

    workspace_id: UUID

    async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport:
        now = datetime.now(UTC)
        cutoff = None if window_seconds is None else now - timedelta(seconds=window_seconds)
        window = tables.ledger.c.workspace_id == self.workspace_id
        if cutoff is not None:
            window &= tables.ledger.c.created_at >= cutoff
        member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
        ledger = await self._ledger(connection, tables.ledger, None, cutoff, now)
        by_member = tuple(
            SubjectTotal(row.member_id, row.email, int(row.tokens), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.member.c.id.label("member_id"),
                    tables.member.c.email,
                    _token_sum().label("tokens"),
                    _token_cost_sum().label("priced"),
                )
                .select_from(
                    tables.ledger.join(tables.turn).join(
                        tables.member, TURN_MEMBER_ID == tables.member.c.id
                    )
                )
                .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
                .group_by(tables.member.c.id, tables.member.c.email)
                .order_by(tables.member.c.email)
            )
        )
        execution = sa.case(
            (
                tables.ledger.c.turn_id.isnot(None),
                sa.func.coalesce(tables.turn.c.subagent_profile, ""),
            )
        ).label("execution")
        agent_rows = await connection.execute(
            sa.select(
                tables.turn.c.agent_id,
                member_name.label("name"),
                execution,
                _token_sum().label("tokens"),
                _token_cost_sum().label("priced"),
            )
            .select_from(
                tables.ledger.outerjoin(tables.turn).outerjoin(
                    tables.agent, tables.turn.c.agent_id == tables.agent.c.id
                )
            )
            .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
            .group_by(tables.turn.c.agent_id, member_name, execution)
            .order_by(member_name.nulls_last(), execution)
        )
        agents: dict[UUID | None, tuple[str, int, int]] = {}
        executions: dict[str, tuple[int, int]] = {}
        for row in agent_rows:
            name = row.name if row.agent_id is not None else WORKSPACE_JOB_LABEL
            _prior_name, prior_tokens, prior_priced = agents.get(row.agent_id, (name, 0, 0))
            agents[row.agent_id] = (
                name,
                prior_tokens + int(row.tokens),
                prior_priced + int(row.priced),
            )
            if row.execution is not None:
                execution_tokens, execution_priced = executions.get(row.execution, (0, 0))
                executions[row.execution] = (
                    execution_tokens + int(row.tokens),
                    execution_priced + int(row.priced),
                )
        by_agent = tuple(
            SubjectTotal(agent_id, name, tokens, priced)
            for agent_id, (name, tokens, priced) in agents.items()
        )
        by_execution = tuple(
            UsageBreakdown(name, tokens, priced)
            for name, (tokens, priced) in sorted(executions.items())
        )
        by_origin = await self._by_origin(connection, window)
        return SpendReport(
            window_seconds,
            ledger.total_micro_usd,
            ledger.by_dimension,
            ledger.by_service,
            by_member,
            by_agent,
            by_origin,
            ledger.by_price_digest,
            replace(ledger.usage, by_execution=by_execution),
        )

    async def _ledger(
        self,
        connection: AsyncConnection,
        source: sa.FromClause,
        execution_column: sa.ColumnElement[str] | None,
        cutoff: datetime | None,
        now: datetime,
    ) -> _LedgerRollup:
        rolled_through = await _rolled_through(connection, self.workspace_id)
        rolled = (
            None
            if rolled_through is None
            else rolled_days(
                self.workspace_id, rolled_through, cutoff, _previous_start(cutoff, now)
            )
        )
        mine = tables.ledger.c.workspace_id == self.workspace_id
        return await _ledger_rollup(
            connection,
            source,
            mine if rolled is None else mine & rolled.skip,
            cutoff,
            now,
            execution_column,
            rolled,
        )

    async def _by_origin(
        self, connection: AsyncConnection, window: sa.ColumnElement[bool]
    ) -> tuple[OriginTotal, ...]:
        """Folded to one row per turn before the climb: joining raw rows carried 685k into a hash
        join whose spill cost 11 of the page's 15 seconds; folded, it meets 70k and costs 1."""
        turn = tables.turn
        spent = sa.select(tables.ledger.c.turn_id).where(
            window,
            tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS),
            tables.ledger.c.turn_id.isnot(None),
        )
        chain = (
            sa.select(
                turn.c.id.label("turn_id"),
                turn.c.parent_turn_id.label("parent"),
                turn.c.conversation_id.label("conversation_id"),
            )
            .where(turn.c.id.in_(spent))
            .cte("spend_origin_chain", recursive=True)
        )
        ancestor = turn.alias("origin_ancestor")
        chain = chain.union_all(
            sa.select(
                chain.c.turn_id,
                ancestor.c.parent_turn_id,
                ancestor.c.conversation_id,
            ).select_from(chain.join(ancestor, ancestor.c.id == chain.c.parent))
        )
        roots = (
            sa.select(chain.c.turn_id, chain.c.conversation_id)
            .where(chain.c.parent.is_(None))
            .subquery("spend_origin_root")
        )
        conversation = tables.conversation.alias("origin_conversation")
        label = sa.func.coalesce(
            conversation.c.surface_label, conversation.c.surface, WORKSPACE_JOB_LABEL
        ).label("label")
        spent_per_turn = (
            sa.select(
                tables.ledger.c.turn_id.label("turn_id"),
                sa.func.sum(tables.ledger.c.amount).label("tokens"),
                sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
            )
            .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
            .group_by(tables.ledger.c.turn_id)
            .subquery("spend_per_turn")
        )
        return tuple(
            OriginTotal(row.surface, row.label, int(row.tokens), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    conversation.c.surface,
                    label,
                    sa.func.sum(spent_per_turn.c.tokens).label("tokens"),
                    sa.func.sum(spent_per_turn.c.priced).label("priced"),
                )
                .select_from(
                    spent_per_turn.outerjoin(
                        roots, roots.c.turn_id == spent_per_turn.c.turn_id
                    ).outerjoin(conversation, conversation.c.id == roots.c.conversation_id)
                )
                .group_by(conversation.c.surface, label)
                .order_by(sa.desc("priced"))
            )
        )

    async def read_totals(
        self, connection: AsyncConnection, window_seconds: int | None
    ) -> SpendTotals:
        """The workspace totals `read` reports, without reading its member, agent, or origin
        breakdowns."""
        now = datetime.now(UTC)
        cutoff = None if window_seconds is None else now - timedelta(seconds=window_seconds)
        ledger = await self._ledger(
            connection,
            tables.ledger.outerjoin(tables.turn),
            tables.turn.c.subagent_profile,
            cutoff,
            now,
        )
        return SpendTotals(
            window_seconds,
            ledger.total_micro_usd,
            ledger.by_dimension,
            ledger.by_service,
            ledger.usage,
        )

    async def read_member(
        self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None
    ) -> MemberSpendReport:
        """One member's selected and all-time usage plus their member-scoped caps. Ledger rows
        reach the member through each turn's own member — its speaker, else the member it acts
        for — as member caps do, so a member's spend in a workspace conversation is theirs."""
        now = datetime.now(UTC)
        cutoff = None if window_seconds is None else now - timedelta(seconds=window_seconds)
        ledger = await _ledger_rollup(
            connection,
            tables.ledger.join(tables.turn),
            (tables.ledger.c.workspace_id == self.workspace_id) & (TURN_MEMBER_ID == member_id),
            cutoff,
            now,
            tables.turn.c.subagent_profile,
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
            ledger.total_micro_usd,
            ledger.by_dimension,
            caps,
            ledger.usage,
        )
