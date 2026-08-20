"""Token pricing, the one billing write per turn, and the spend caps decided against the ledger."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.balance import (
    _forget_absent_balance,
    balance_refusal_message,
    debit,
    read_headroom,
)
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
from ufo.schema import tables
from ufo.schema.records import TurnStatus, Usage, ledger_id_for

MICRO_USD_PER_USD = 1_000_000

TOKENS_DIMENSION = "tokens"
EGRESS_DIMENSION = "egress"
SANDBOX_TOKENS_DIMENSION = "sandbox_tokens"
SANDBOX_TOKENS_ATTEMPT = "sandbox"
IMAGES_DIMENSION = "images"
VIDEOS_DIMENSION = "videos"

CapScope = Literal["workspace", "member", "agent"]
WORKSPACE_SCOPE: CapScope = "workspace"
MEMBER_SCOPE: CapScope = "member"
AGENT_SCOPE: CapScope = "agent"

OnBreach = Literal["park", "reject"]
PARK: OnBreach = "park"
REJECT: OnBreach = "reject"

QUEUED: TurnStatus = "queued"


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


async def workspace_owns_the_key(
    connection: AsyncConnection, workspace_id: UUID, key_slot: str | None
) -> bool:
    """Whether the workspace's own provider key served this burn — the same rule the usage export
    labels `byok` with, so the balance and the export never disagree about who paid."""
    if not key_slot:
        return False
    return (
        await connection.execute(
            sa.select(tables.credential.c.slot).where(
                tables.credential.c.workspace_id == workspace_id,
                tables.credential.c.slot == key_slot,
            )
        )
    ).one_or_none() is not None


async def record_turn_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
    attempt: str = "",
    pricing: Pricing = CORE_PRICING,
    byok: bool = False,
) -> None:
    """One billing write per turn per run attempt; select-then-insert is replay-safe because DBOS
    re-executes a given attempt sequentially, never concurrently with itself. A turn parked mid-run
    and resumed spends under a fresh attempt (workflow id), so each partial burn is billed once and
    the ledger reflects the true total the provider charged — never a lost burn, never a double.

    The row carries the burn's prompt split beside its total, so a terminal frame's cache share is a
    read of the same row the tokens, cost and model come off rather than a second account of the
    same spend.

    What the burn cost is what comes off the balance: one price, no margin. The debit shares this
    transaction and sits after the replay guard, so a re-executed attempt takes nothing twice.

    A burn the workspace's own provider key paid for debits nothing: the workspace already paid the
    provider directly, and taking it off the balance too would charge twice for one call.
    `byok` is decided once, when the turn is set up and against the key that will serve it, rather
    than re-read here: credential rows can change while a turn runs, so a value read at terminal
    would make a turn free because a key arrived mid-run, or charge for one the workspace's own key
    paid because a key left. It is the same question the usage export freezes at mint."""
    total = _total_tokens(usage)
    if total == 0:
        return
    ledger_id = ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, attempt)
    already = await connection.execute(
        sa.select(tables.ledger.c.id).where(tables.ledger.c.id == ledger_id)
    )
    if already.one_or_none() is not None:
        return
    priced = pricing.micro_usd(model, usage)
    billed = 0 if byok else priced
    taken = await debit(connection, workspace_id, billed)
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
            debited_micro_usd=taken,
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
    byok: bool = False,
) -> None:
    """Bill a background job's metered model call to the workspace, not a turn: one priced `tokens`
    row with `turn_id` NULL, stamped with the model and price digest exactly as a turn's tokens are.
    The row carries a fresh id, so each genuine completion is billed once — there is no DBOS step
    checkpoint around a job's model call, so a workflow replay re-invokes the provider (a real
    charge) and bills that invocation, never a phantom double or a lost burn. It lands in the
    workspace spend total and every workspace-scoped cap window (which sum by `workspace_id`), and
    is excluded from per-member and per-agent attribution (which join through `turn` — a NULL FK
    drops out), because a job's spend belongs to no member or agent.

    It bills against the serving model's own rate: no agent authored this call, so there is no
    delegated choice to charge for and nothing to route. A job served by the workspace's own key
    bills nothing, exactly as a turn does."""
    total = _total_tokens(usage)
    if total == 0:
        return
    priced = pricing.micro_usd(model, usage)
    billed = 0 if byok else priced
    taken = await debit(connection, workspace_id, billed)
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=uuid4(),
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
            debited_micro_usd=taken,
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


async def record_probe_egress_request(
    connection: AsyncConnection, workspace_id: UUID, amount: int = 1
) -> None:
    """Meter an off-turn probe's sandbox egress under the same `egress` dimension a turn's is: a
    request COUNT priced at zero, on a row whose `turn_id` is NULL because a probe runs off every
    turn. Reaching the network from a conversation's sandbox is one act with one meaning whether a
    turn or a probe made it, so it is one dimension — the NULL FK is what separates them, and it
    separates them the way a background job's model spend is separated from a turn's
    (`record_workspace_usage`): the row lands in the workspace total and every workspace-scoped cap
    window, and drops out of per-member and per-agent attribution, which join through `turn`.

    The row carries a fresh id rather than a key derived from the probe, so each flushed batch bills
    once and nothing accumulates onto a key a later probe could reuse; the proxy sums a batch per
    principal before writing, so a probe's several CONNECTs in one window are one row."""
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=uuid4(),
            workspace_id=workspace_id,
            turn_id=None,
            dimension=EGRESS_DIMENSION,
            amount=amount,
            priced_micro_usd=0,
            model="",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
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
    `record_turn_usage` writes for the same turn. The row carries the burn's prompt split beside its
    total, exactly as the host row does, so `read_turn_cost` reads this dimension's cache share off
    the same row its tokens and cost come from.

    The model host's key comes from the proxy's environment, so an in-sandbox call is always served
    by the platform: it debits the balance whatever key the workspace holds for the host loop."""
    total = _total_tokens(usage)
    if total == 0:
        return
    priced = pricing.micro_usd(model, usage)
    prompt = _prompt_tokens(usage)
    ledger_id = ledger_id_for(
        workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION, SANDBOX_TOKENS_ATTEMPT
    )
    taken = await debit(connection, workspace_id, priced)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    await connection.execute(
        insert(tables.ledger)
        .values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension=SANDBOX_TOKENS_DIMENSION,
            amount=total,
            prompt_tokens=prompt,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_5m_tokens=usage.cache_write_5m_tokens,
            cache_write_30m_tokens=usage.cache_write_30m_tokens,
            cache_write_1h_tokens=usage.cache_write_1h_tokens,
            token_classes_complete=True,
            priced_micro_usd=priced,
            debited_micro_usd=taken,
            model=model,
            price_digest=pricing.digest,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={
                "amount": tables.ledger.c.amount + total,
                "prompt_tokens": tables.ledger.c.prompt_tokens + prompt,
                "input_tokens": tables.ledger.c.input_tokens + usage.input_tokens,
                "output_tokens": tables.ledger.c.output_tokens + usage.output_tokens,
                "cache_read_tokens": tables.ledger.c.cache_read_tokens + usage.cache_read_tokens,
                "cache_write_5m_tokens": tables.ledger.c.cache_write_5m_tokens
                + usage.cache_write_5m_tokens,
                "cache_write_30m_tokens": tables.ledger.c.cache_write_30m_tokens
                + usage.cache_write_30m_tokens,
                "cache_write_1h_tokens": tables.ledger.c.cache_write_1h_tokens
                + usage.cache_write_1h_tokens,
                "token_classes_complete": tables.ledger.c.token_classes_complete,
                "priced_micro_usd": tables.ledger.c.priced_micro_usd + priced,
                "debited_micro_usd": tables.ledger.c.debited_micro_usd + taken,
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
    await _record_media_usage(
        connection, workspace_id, turn_id, IMAGES_DIMENSION, model, images, micro_usd
    )


async def record_video_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    videos: int,
    micro_usd: int,
) -> None:
    """Meter generated videos as a `videos` ledger row per turn, on the same terms as
    `record_image_usage`: `amount` counts the videos, `priced_micro_usd` is what the provider
    charged for them, and the caller prices the call because a video model is not a `ModelSpec` and
    its unit is an output second rather than a token."""
    await _record_media_usage(
        connection, workspace_id, turn_id, VIDEOS_DIMENSION, model, videos, micro_usd
    )


async def _record_media_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    dimension: str,
    model: str,
    amount: int,
    micro_usd: int,
) -> None:
    """Meter one image or video generation, accumulating onto a per-turn row of its dimension.

    It debits its own increment, exactly as a token burn does: a generated image or video is real
    money on the platform key, so a balance that did not move for it would let an empty workspace
    keep generating. `egress` is the one metered dimension that never debits — it counts requests
    and prices at zero."""
    ledger_id = ledger_id_for(workspace_id, turn_id, dimension)
    taken = await debit(connection, workspace_id, micro_usd)
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
            debited_micro_usd=taken,
            model=model,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={
                "amount": tables.ledger.c.amount + amount,
                "priced_micro_usd": tables.ledger.c.priced_micro_usd + micro_usd,
                "debited_micro_usd": tables.ledger.c.debited_micro_usd + taken,
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
    insert-once and settles at creation; a `sandbox_tokens`, `images` or `videos` row accumulates
    until its turn is terminal, and `EXPORT_SETTLE_MARGIN_SECONDS` past `turn.updated_at` only
    bounds how often a late egress-proxy write costs an extra top-up intent — a row that grows
    after minting mints a further intent from the prior high-water mark, so no growth is ever lost
    to timing.
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
                tables.ledger.c.byok,
                tables.ledger.c.token_classes_complete,
                tables.turn.c.byok.label("turn_byok"),
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
                    tables.ledger.c.dimension.in_(
                        (SANDBOX_TOKENS_DIMENSION, IMAGES_DIMENSION, VIDEOS_DIMENSION)
                    )
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


@dataclass(frozen=True, slots=True)
class SpendDecision:
    outcome: SpendOutcome
    message: str


@dataclass(frozen=True)
class SpendEvaluator:
    """Decide whether a turn may run under the workspace's caps: every cap that applies to this
    turn's workspace, member, and agent is read, the priced ledger summed over each cap's rolling
    window, and the answer is allow / park / reject. Every applicable cap must have headroom (the
    tightest binds); a breach parks unless any breached cap rejects, in which case reject wins.
    `decide` is the whole workflow, its `_` steps beneath it in execution order; the caller supplies
    the connection so the same decision runs inside an admission transaction or a fresh read at a
    mid-turn step."""

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
class SpendReport:
    """A selected range and all-time workspace ledger, with daily, execution, model, dimension,
    member, agent, origin, and price-table totals."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_member: tuple[SubjectTotal, ...]
    by_agent: tuple[SubjectTotal, ...]
    by_origin: tuple[OriginTotal, ...]
    by_price_digest: tuple[PriceDigestTotal, ...]
    usage: UsageDetails


@dataclass(frozen=True, slots=True)
class SpendCapLine:
    window_seconds: int
    limit_micro_usd: int
    on_breach: OnBreach


@dataclass(frozen=True, slots=True)
class AgentSpendReport:
    """One agent's selected range and all-time ledger, including its subagent turns and caps."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    caps: tuple[SpendCapLine, ...]
    usage: UsageDetails


@dataclass(frozen=True, slots=True)
class MemberSpendReport:
    """One member's selected range and all-time ledger, naming no other member or agent."""

    window_seconds: int | None
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    caps: tuple[SpendCapLine, ...]
    usage: UsageDetails


TOKEN_DIMENSIONS = (TOKENS_DIMENSION, SANDBOX_TOKENS_DIMENSION)
WORKSPACE_JOB_LABEL = "Workspace jobs"


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


async def _usage_details(
    connection: AsyncConnection,
    source: sa.FromClause,
    scope: sa.ColumnElement[bool],
    cutoff: datetime | None,
    now: datetime,
) -> UsageDetails:
    selected_scope = scope if cutoff is None else scope & (tables.ledger.c.created_at >= cutoff)
    selected_row = (
        await connection.execute(
            sa.select(
                _token_sum().label("tokens"),
                _token_cost_sum().label("token_cost"),
                sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0).label("cost"),
            )
            .select_from(source)
            .where(selected_scope)
        )
    ).one()
    all_time_row = (
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
    day = sa.func.date(tables.ledger.c.created_at).label("day")
    daily_rows = {
        date.fromisoformat(str(row.day)): DailyUsageTotal(
            day=str(row.day),
            tokens=int(row.tokens),
            token_micro_usd=int(row.token_cost),
            total_micro_usd=int(row.cost),
        )
        for row in await connection.execute(
            sa.select(
                day,
                _token_sum().label("tokens"),
                _token_cost_sum().label("token_cost"),
                sa.func.sum(tables.ledger.c.priced_micro_usd).label("cost"),
            )
            .select_from(source)
            .where(selected_scope)
            .group_by(day)
            .order_by(day)
        )
    }
    first_day = cutoff.date() if cutoff is not None else min(daily_rows, default=None)
    daily: tuple[DailyUsageTotal, ...] = ()
    if first_day is not None:
        days = (now.date() - first_day).days + 1
        daily = tuple(
            daily_rows.get(current, DailyUsageTotal(str(current), 0, 0, 0))
            for current in (first_day + timedelta(days=offset) for offset in range(days))
        )
    execution = sa.func.coalesce(tables.turn.c.subagent_profile, "").label("execution")
    by_execution = tuple(
        UsageBreakdown(row.execution, int(row.tokens), int(row.priced))
        for row in await connection.execute(
            sa.select(
                execution,
                sa.func.sum(tables.ledger.c.amount).label("tokens"),
                sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
            )
            .select_from(source)
            .where(
                selected_scope,
                tables.ledger.c.turn_id.isnot(None),
                tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS),
            )
            .group_by(execution)
            .order_by(execution)
        )
    )
    by_model = tuple(
        UsageBreakdown(row.model, int(row.tokens), int(row.priced))
        for row in await connection.execute(
            sa.select(
                tables.ledger.c.model,
                sa.func.sum(tables.ledger.c.amount).label("tokens"),
                sa.func.sum(tables.ledger.c.priced_micro_usd).label("priced"),
            )
            .select_from(source)
            .where(selected_scope, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
            .group_by(tables.ledger.c.model)
            .order_by(tables.ledger.c.model)
        )
    )
    previous_tokens: int | None = None
    if cutoff is not None:
        previous_start = cutoff - (now - cutoff)
        previous_tokens = int(
            (
                await connection.execute(
                    sa.select(_token_sum())
                    .select_from(source)
                    .where(
                        scope,
                        tables.ledger.c.created_at >= previous_start,
                        tables.ledger.c.created_at < cutoff,
                    )
                )
            ).scalar_one()
        )
    first_used_at = all_time_row.first_used_at
    if first_used_at is not None and first_used_at.tzinfo is None:
        first_used_at = first_used_at.replace(tzinfo=UTC)
    return UsageDetails(
        selected=UsageTotal(
            int(selected_row.tokens), int(selected_row.token_cost), int(selected_row.cost)
        ),
        all_time=UsageTotal(
            int(all_time_row.tokens), int(all_time_row.token_cost), int(all_time_row.cost)
        ),
        first_used_at=first_used_at,
        previous_tokens=previous_tokens,
        daily=daily,
        by_execution=by_execution,
        by_model=by_model,
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
            SubjectTotal(row.member_id, row.email, int(row.tokens), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    tables.conversation.c.member_id,
                    tables.member.c.email,
                    _token_sum().label("tokens"),
                    _token_cost_sum().label("priced"),
                )
                .select_from(
                    tables.ledger.join(tables.turn)
                    .join(
                        tables.conversation,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                    .join(tables.member, tables.conversation.c.member_id == tables.member.c.id)
                )
                .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
                .group_by(tables.conversation.c.member_id, tables.member.c.email)
                .order_by(tables.member.c.email)
            )
        )
        by_agent = tuple(
            SubjectTotal(
                row.agent_id,
                row.name if row.agent_id is not None else WORKSPACE_JOB_LABEL,
                int(row.tokens),
                int(row.priced),
            )
            for row in await connection.execute(
                sa.select(
                    tables.turn.c.agent_id,
                    tables.agent.c.name,
                    _token_sum().label("tokens"),
                    _token_cost_sum().label("priced"),
                )
                .select_from(
                    tables.ledger.outerjoin(tables.turn).outerjoin(
                        tables.agent, tables.turn.c.agent_id == tables.agent.c.id
                    )
                )
                .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
                .group_by(tables.turn.c.agent_id, tables.agent.c.name)
                .order_by(tables.agent.c.name.nulls_last())
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
        by_origin = await self._by_origin(connection, window)
        usage = await _usage_details(
            connection,
            tables.ledger.outerjoin(tables.turn),
            tables.ledger.c.workspace_id == self.workspace_id,
            cutoff,
            now,
        )
        return SpendReport(
            window_seconds,
            total,
            by_dimension,
            by_member,
            by_agent,
            by_origin,
            by_price_digest,
            usage,
        )

    async def _by_origin(
        self, connection: AsyncConnection, window: sa.ColumnElement[bool]
    ) -> tuple[OriginTotal, ...]:
        """Token spend gathered under the conversation that started it, climbing `parent_turn_id`
        so a subagent's burn lands on the origin that spawned it rather than on the private
        conversation it ran in — which carries no surface a member ever saw. Half of a chat
        surface's spend is that fan-out, so the ungathered sum answers a question nobody asked.

        The climb seeds on the turns the window's own ledger names, so it walks what actually spent
        rather than the workspace's whole history, and each step is a primary-key lookup on the
        parent. It seeds one row per turn and not one per ledger row: a turn holds a token row per
        dimension and per resumed attempt, and seeding on the rows themselves would pair each of
        them with every root the turn contributed, multiplying the origin's total by their count. A
        turn-less row (a background job) reaches no conversation and gathers under `Workspace jobs`,
        as it does per agent."""
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
        return tuple(
            OriginTotal(row.surface, row.label, int(row.tokens), int(row.priced))
            for row in await connection.execute(
                sa.select(
                    conversation.c.surface,
                    label,
                    _token_sum().label("tokens"),
                    _token_cost_sum().label("priced"),
                )
                .select_from(
                    tables.ledger.outerjoin(
                        roots, roots.c.turn_id == tables.ledger.c.turn_id
                    ).outerjoin(conversation, conversation.c.id == roots.c.conversation_id)
                )
                .where(window, tables.ledger.c.dimension.in_(TOKEN_DIMENSIONS))
                .group_by(conversation.c.surface, label)
                .order_by(sa.desc("priced"))
            )
        )

    async def read_agent(
        self, connection: AsyncConnection, agent_id: UUID, window_seconds: int | None
    ) -> AgentSpendReport:
        """One agent's selected and all-time usage plus its agent-scoped caps. Turn-less workspace
        usage stays in the workspace rollup."""
        now = datetime.now(UTC)
        cutoff = None if window_seconds is None else now - timedelta(seconds=window_seconds)
        window = (tables.ledger.c.workspace_id == self.workspace_id) & (
            tables.turn.c.agent_id == agent_id
        )
        if cutoff is not None:
            window &= tables.ledger.c.created_at >= cutoff
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
            await _usage_details(
                connection,
                joined,
                (tables.ledger.c.workspace_id == self.workspace_id)
                & (tables.turn.c.agent_id == agent_id),
                cutoff,
                now,
            ),
        )

    async def read_member(
        self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None
    ) -> MemberSpendReport:
        """One member's selected and all-time usage plus their member-scoped caps. Ledger rows
        reach the member through each turn's conversation, as member caps do."""
        now = datetime.now(UTC)
        cutoff = None if window_seconds is None else now - timedelta(seconds=window_seconds)
        joined = tables.ledger.join(tables.turn).join(
            tables.conversation, tables.turn.c.conversation_id == tables.conversation.c.id
        )
        window = (tables.ledger.c.workspace_id == self.workspace_id) & (
            tables.conversation.c.member_id == member_id
        )
        if cutoff is not None:
            window &= tables.ledger.c.created_at >= cutoff
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
            await _usage_details(
                connection,
                joined,
                (tables.ledger.c.workspace_id == self.workspace_id)
                & (tables.conversation.c.member_id == member_id),
                cutoff,
                now,
            ),
        )


@dataclass(frozen=True)
class BalanceGate:
    """Whether a workspace's prepaid balance lets a turn begin, and whether it lets a running one
    continue. The two are deliberately different lines.

    A single threshold thrashes. If entry and continuation both tested against the reserve, a
    balance sitting just above it would admit or resume a turn, the first round's spend would push
    it back under, and the turn would park again — burning a round per cycle and answering nothing,
    so a small credit would buy a loop rather than progress.

    So `reserve_micro_usd` means one thing: the headroom a turn needs to *begin*. Stopping happens
    at zero. Overshoot is then bounded by one round below zero rather than running unbounded beneath
    the reserve, and a credit smaller than the reserve cannot resume anything.

    A workspace whose card has already paid carries both lines down by its grace, which is what
    keeps a solvent workspace working across the gap between falling under its refill threshold and
    the charge landing. The lines move together and by the same figure, so entry stays the stricter
    of the two: were the grace applied only to entry, a turn would be admitted below the line that
    stops it and be parked on its first round.

    A workspace with no balance row allows both, which is the self-host path.

    `billing_url` is the deploy's billing screen, threaded from boot, which the refusal names as
    where an admin adds credit. None on a deploy that has no such screen — the refusal then names
    the act without a screen."""

    workspace_id: UUID
    billing_url: str | None = None

    async def admits(
        self,
        connection: AsyncConnection,
        agent_id: UUID | None = None,
        key_slot_for: Callable[[str], str | None] | None = None,
        turn_id: UUID | None = None,
        model: str | None = None,
    ) -> SpendDecision:
        """Whether a turn may be admitted, folded into a live one, or resumed after a park.

        A turn the workspace's own provider key will serve is admitted while the balance is not
        overdrawn: its model rounds debit nothing, so the balance can never rise to clear the
        reserve and refusing at that line would lock the workspace out for good — including out of
        the documented way to keep working without buying credit. Deciding it needs the model that
        will run, which is why the caller supplies the resolution rather than the gate reaching for
        a registry it has no business holding; `model` names that model where the caller already
        knows it, because a spawned child runs its profile's model rather than its agent's.

        The exemption stops at zero all the same. A turn served by the workspace's own key still
        generates media and still makes in-sandbox calls on the platform's key, and those debit — so
        an exemption that ignored the balance would let an overdrawn workspace spend the platform's
        money without bound, one turn at a time, forever.

        The own-key exemption starts free work; it does not resume a turn that already charged. A
        parked turn that took something off the balance would otherwise be readmitted every sweep,
        park again on its next real charge, and cycle without answering."""
        headroom = await read_headroom(connection, self.workspace_id)
        if headroom is None:
            return SpendDecision(outcome=ALLOW, message="")
        _forget_absent_balance(self.workspace_id)
        if headroom.balance_micro_usd > headroom.reserve_micro_usd - headroom.grace_micro_usd:
            return SpendDecision(outcome=ALLOW, message="")
        if turn_id is not None and await self._turn_has_debited(connection, turn_id):
            return SpendDecision(outcome=REJECT, message=balance_refusal_message(self.billing_url))
        if headroom.balance_micro_usd > 0 and await self._workspace_serves_itself(
            connection, agent_id, key_slot_for, model
        ):
            return SpendDecision(outcome=ALLOW, message="")
        return SpendDecision(outcome=REJECT, message=balance_refusal_message(self.billing_url))

    async def _workspace_serves_itself(
        self,
        connection: AsyncConnection,
        agent_id: UUID | None,
        key_slot_for: Callable[[str], str | None] | None,
        model: str | None = None,
    ) -> bool:
        if key_slot_for is None:
            return False
        if model is None:
            if agent_id is None:
                return False
            model = (
                await connection.execute(
                    sa.select(tables.agent.c.model).where(
                        tables.agent.c.id == agent_id,
                        tables.agent.c.workspace_id == self.workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if model is None:
                return False
        return await workspace_owns_the_key(connection, self.workspace_id, key_slot_for(model))

    async def sustains(
        self,
        connection: AsyncConnection,
        pending_micro_usd: int,
        turn_id: UUID | None = None,
    ) -> SpendDecision:
        """Whether a running turn may take another round, with this attempt's unbilled spend priced
        in. It stops at zero, not at the reserve, so the reserve stays headroom to start with.

        A round that will debit nothing is never held: parking a turn whose spend the balance does
        not fund leaves the balance exactly where it was, so the resume that follows parks it again
        at the same point, forever. Only spend that would actually push the balance under stops
        it."""
        headroom = await read_headroom(connection, self.workspace_id)
        if headroom is None:
            return SpendDecision(outcome=ALLOW, message="")
        _forget_absent_balance(self.workspace_id)
        if headroom.balance_micro_usd - pending_micro_usd > -headroom.grace_micro_usd:
            return SpendDecision(outcome=ALLOW, message="")
        if pending_micro_usd > 0 or await self._turn_has_debited(connection, turn_id):
            return SpendDecision(outcome=REJECT, message=balance_refusal_message(self.billing_url))
        return SpendDecision(outcome=ALLOW, message="")

    async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool:
        """Whether this turn has already taken anything off the balance.

        A turn whose model rounds run on the workspace's own key still generates images and video
        and still makes in-sandbox calls on the platform key, and those charge. Asking only whether
        the next round costs anything would let such a turn run on past zero with nothing to stop
        it; asking only whether the balance is under would park a turn that debits nothing, which it
        can never resume from. So the question is whether the spend in front of this turn actually
        charges the balance.

        It reads what the rows took rather than what they cost: an own-key row is priced and takes
        nothing, so pricing would park a turn that can never resume its way out."""
        if turn_id is None:
            return False
        return (
            await connection.execute(
                sa.select(tables.ledger.c.id)
                .where(
                    tables.ledger.c.turn_id == turn_id,
                    tables.ledger.c.debited_micro_usd > 0,
                )
                .limit(1)
            )
        ).one_or_none() is not None
