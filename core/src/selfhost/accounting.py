"""Token pricing, the one billing write per turn, and the spend caps decided against the ledger."""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import Usage, ledger_id_for

TOKENS_PER_MTOK = 1_000_000
MICRO_USD_PER_USD = 1_000_000

TOKENS_DIMENSION = "tokens"
EGRESS_DIMENSION = "egress"

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


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Micro-USD per million tokens, one rate per token class."""

    input: int
    output: int
    cache_read: int
    cache_write: int


MODEL_TOKEN_PRICE: dict[str, ModelPrice] = {
    "claude-fable-5": ModelPrice(10_000_000, 50_000_000, 1_000_000, 12_500_000),
    "claude-opus-4-8": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-opus-4-7": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-opus-4-6": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-sonnet-5": ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    "claude-sonnet-4-6": ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    "claude-haiku-4-5": ModelPrice(1_000_000, 5_000_000, 100_000, 1_250_000),
    "gpt-5.5": ModelPrice(5_000_000, 30_000_000, 500_000, 5_000_000),
    "gpt-5.4": ModelPrice(2_500_000, 15_000_000, 250_000, 2_500_000),
    "gpt-5.4-mini": ModelPrice(750_000, 4_500_000, 75_000, 750_000),
    "gpt-5.4-nano": ModelPrice(200_000, 1_250_000, 20_000, 200_000),
}


def price_digest() -> str:
    """A deterministic version stamp of the price table: sha256 over the sorted per-model rates.
    Stamped on every priced ledger row so a burn stays attributable to the rate that priced it —
    after a MODEL_TOKEN_PRICE edit historical rows keep their original digest and reprice/audit
    reconciliation over a window that spans the change stays exact."""
    payload = json.dumps(
        {
            model: {
                "input": price.input,
                "output": price.output,
                "cache_read": price.cache_read,
                "cache_write": price.cache_write,
            }
            for model, price in sorted(MODEL_TOKEN_PRICE.items())
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


PRICE_DIGEST = price_digest()


def usage_priced_micro_usd(model: str, usage: Usage) -> int:
    """Micro-USD for a usage split: integer dot product, floored at micro-dollar precision.

    An unknown model warns loudly and prices at zero — never a silent fallback to another
    model's price, and never a raise: pricing runs inside the turn's terminal commit, so raising
    would wedge the commit-retry loop instead of ending the client's wait."""
    price = MODEL_TOKEN_PRICE.get(model)
    if price is None:
        log("pricing.unknown_model", model=model)
        return 0
    micro_usd_mtok = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return micro_usd_mtok // TOKENS_PER_MTOK


async def record_turn_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
    attempt: str = "",
) -> None:
    """One billing write per turn per run attempt; select-then-insert is replay-safe because DBOS
    re-executes a given attempt sequentially, never concurrently with itself. A turn parked mid-run
    and resumed spends under a fresh attempt (workflow id), so each partial burn is billed once and
    the ledger reflects the true total the provider charged — never a lost burn, never a double."""
    total = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
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
            priced_micro_usd=usage_priced_micro_usd(model, usage),
            model=model,
            price_digest=PRICE_DIGEST,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def read_turn_cost(
    connection: AsyncConnection, turn_id: UUID
) -> tuple[int, int, str] | None:
    """The billed tokens, micro-USD, and model for a turn, summed across its run attempts; None when
    nothing was billed. A parked-then-resumed turn has one ledger row per attempt, so the terminal
    cost is their total — the true provider charge."""
    row = (
        await connection.execute(
            sa.select(
                sa.func.sum(tables.ledger.c.amount),
                sa.func.sum(tables.ledger.c.priced_micro_usd),
                sa.func.max(tables.ledger.c.model),
            ).where(
                (tables.ledger.c.turn_id == turn_id)
                & (tables.ledger.c.dimension == TOKENS_DIMENSION)
            )
        )
    ).one()
    if row[0] is None:
        return None
    return int(row[0]), int(row[1]), row[2]


async def record_egress_request(
    connection: AsyncConnection, workspace_id: UUID, turn_id: UUID
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
            amount=1,
            priced_micro_usd=0,
            model="",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.ledger.c.id],
            set_={"amount": tables.ledger.c.amount + 1, "updated_at": sa.func.now()},
        )
    )


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
            if await self._used_micro_usd(connection, cap) + pending_micro_usd
            > cap.limit_micro_usd
        ]
        if not breaches:
            return SpendDecision(outcome=ALLOW, message="")
        outcome: SpendOutcome = (
            REJECT if any(cap.on_breach == REJECT for cap in breaches) else PARK
        )
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
                    .select_from(tables.ledger.join(tables.turn).join(tables.conversation))
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
    the reconciliation seam over a window that spans a MODEL_TOKEN_PRICE change."""

    window_seconds: int
    total_micro_usd: int
    by_dimension: tuple[DimensionTotal, ...]
    by_member: tuple[SubjectTotal, ...]
    by_agent: tuple[SubjectTotal, ...]
    by_price_digest: tuple[PriceDigestTotal, ...]


@dataclass(frozen=True)
class SpendRollup:
    """Sum the workspace's ledger over a rolling window for the `selfhost spend` CLI and the web
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
                    .join(tables.conversation)
                    .join(tables.member)
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
