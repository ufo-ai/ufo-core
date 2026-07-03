"""Token pricing, the one billing write per turn, and the spend caps decided against the ledger."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import Usage, ledger_id_for

TOKENS_PER_MTOK = 1_000_000
MICRO_USD_PER_USD = 1_000_000

CapScope = Literal["workspace", "member", "agent"]
WORKSPACE_SCOPE: CapScope = "workspace"
MEMBER_SCOPE: CapScope = "member"
AGENT_SCOPE: CapScope = "agent"

OnBreach = Literal["park", "reject"]
PARK: OnBreach = "park"
REJECT: OnBreach = "reject"

SpendOutcome = Literal["allow", "park", "reject"]
ALLOW: SpendOutcome = "allow"


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
) -> None:
    """One billing write per turn; select-then-insert is replay-safe because DBOS
    re-executes a turn sequentially, never concurrently with itself."""
    total = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    if total == 0:
        return
    ledger_id = ledger_id_for(workspace_id, turn_id, "tokens")
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
            dimension="tokens",
            amount=total,
            priced_micro_usd=usage_priced_micro_usd(model, usage),
            model=model,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def read_turn_cost(
    connection: AsyncConnection, turn_id: UUID
) -> tuple[int, int, str] | None:
    """The billed tokens, micro-USD, and model for a turn; None when nothing was billed."""
    row = (
        await connection.execute(
            sa.select(
                tables.ledger.c.amount, tables.ledger.c.priced_micro_usd, tables.ledger.c.model
            ).where(
                (tables.ledger.c.turn_id == turn_id) & (tables.ledger.c.dimension == "tokens")
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return int(row.amount), int(row.priced_micro_usd), row.model


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
