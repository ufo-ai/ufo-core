"""Spend gates: the Manifest point an extension answers "may this workspace spend" through.

Core calls a gate at points only core controls — admission, a fold, the resume sweep, each model
round, a spawn, an off-turn model call, the owner-scope candidate reads — always on the caller's
own connection, so a gate's reads share the transaction that acts on its answer and a gate that
raises aborts it. Core keeps what no gate can decide for itself: which moment it is, whether the
workspace's own provider key serves the model (`self_funded`), and what a verdict does to the turn.
Caps are core's own gate and decide first; with no gate declared, caps alone decide.

A verdict composes as `reject` over `park` over `allow`, and its message is the first decision's
carrying the winning outcome. The outcome strings are persisted — in `ext_store` refusal marks —
so they never change."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.schema import tables
from ufo.schema.records import ToolIntent

SpendOutcome = Literal["allow", "park", "reject"]
SustainOutcome = Literal["allow", "park"]
ALLOW: Final = "allow"
PARK: Final = "park"
REJECT: Final = "reject"

Moment = Literal["admit", "fold", "resume", "spawn", "off_turn", "status"]
ADMIT_MOMENT: Final = "admit"
FOLD_MOMENT: Final = "fold"
RESUME_MOMENT: Final = "resume"
SPAWN_MOMENT: Final = "spawn"
OFF_TURN_MOMENT: Final = "off_turn"
STATUS_MOMENT: Final = "status"


@dataclass(frozen=True, slots=True)
class SpendDecision:
    outcome: SpendOutcome
    message: str


@dataclass(frozen=True, slots=True)
class SustainDecision:
    """A running turn's verdict. A round already under way has spend to preserve, so a gate can
    hold it and never refuse it."""

    outcome: SustainOutcome
    message: str


ALLOWED = SpendDecision(outcome=ALLOW, message="")
SUSTAINED = SustainDecision(outcome=ALLOW, message="")


@dataclass(frozen=True)
class IntentRef:
    """The prepared intent a turn would dispatch: its wire tool and the object kind and action the
    envelope names, None where it names none."""

    tool: str
    kind: str | None
    action: str | None

    @classmethod
    def of(cls, intent: ToolIntent) -> "IntentRef":
        match intent.input:
            case {"kind": str() as kind, "action": str() as action}:
                return cls(tool=intent.tool, kind=kind, action=action)
            case {"kind": str() as kind}:
                return cls(tool=intent.tool, kind=kind, action=None)
        return cls(tool=intent.tool, kind=None, action=None)


@dataclass(frozen=True)
class SpendAsk:
    """What a gate decides on. `model` is the one that will answer, where the moment knows it;
    `self_funded` is core's answer to whether the workspace's own key pays for that model — or, with
    no model, for any model of the deploy. `member_admission` marks a member's own message that
    core would hold rather than refuse if the gates alone stopped it — false where a cap already
    stops it, so the gate words a refusal."""

    workspace_id: UUID
    moment: Moment
    agent_id: UUID | None
    turn_id: UUID | None
    model: str | None
    self_funded: bool
    member_admission: bool
    intent: IntentRef | None


@dataclass(frozen=True)
class Charge:
    """One positive priced delta a ledger write booked, past its replay guard: a replayed snapshot
    books none. `platform_paid` is false where the workspace's own key paid the provider."""

    ledger_id: UUID
    workspace_id: UUID
    turn_id: UUID | None
    dimension: str
    delta_micro_usd: int
    platform_paid: bool


@dataclass(frozen=True)
class GateDeploy:
    """The deploy facts a gate is built from once at boot."""

    public_base_url: str | None
    home_surface: str | None


class SpendGate(Protocol):
    """Every method but `absent` runs on the caller's connection with only that connection and a
    frozen payload; an exception propagates and aborts the caller's transaction."""

    async def admit(self, connection: AsyncConnection, ask: SpendAsk) -> SpendDecision: ...

    async def sustain(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        pending_micro_usd: int,
    ) -> SustainDecision: ...

    def admits_sql(
        self, workspace_id: sa.ColumnElement[UUID], self_funded: sa.ColumnElement[bool]
    ) -> sa.ColumnElement[bool] | None:
        """True where the workspace may run, correlated on `workspace_id`; None to filter
        nothing."""
        ...

    async def charged(self, connection: AsyncConnection, charge: Charge) -> None: ...

    def absent(self, workspace_id: UUID) -> bool:
        """True only when this gate cannot hold `workspace_id`'s next round — the per-round check's
        no-database fast path."""
        ...


@dataclass(frozen=True)
class SpendGateSpec:
    name: str
    build: Callable[[GateDeploy], SpendGate]


def built_gates(specs: Sequence[SpendGateSpec], deploy: GateDeploy) -> tuple[SpendGate, ...]:
    """Build the active gates in lockfile order. A name two specs share fails loud."""
    names = [spec.name for spec in specs]
    if duplicated := sorted({name for name in names if names.count(name) > 1}):
        raise ValueError(f"spend gates declared twice: {', '.join(duplicated)}")
    return tuple(spec.build(deploy) for spec in specs)


def composed(*decisions: SpendDecision) -> SpendDecision:
    """`reject` over `park` over `allow`; the message of the first decision carrying the winner."""
    for outcome in (REJECT, PARK):
        winner = next((decision for decision in decisions if decision.outcome == outcome), None)
        if winner is not None:
            return winner
    return ALLOWED


@dataclass(frozen=True)
class SpendGates:
    """The deploy's gates, built once at boot, with the model key resolution core decides
    `self_funded` by. With no gate every answer is allow and nothing reads the database."""

    gates: tuple[SpendGate, ...] = ()
    key_slot_for: Callable[[str], str | None] | None = None
    own_key_slots: tuple[str, ...] = ()

    async def admit(
        self,
        connection: AsyncConnection,
        moment: Moment,
        workspace_id: UUID,
        *,
        agent_id: UUID | None = None,
        turn_id: UUID | None = None,
        model: str | None = None,
        member_admission: bool = False,
        intent: IntentRef | None = None,
    ) -> SpendDecision:
        """Every gate's verdict on work starting now, composed. `model` defaults to the agent's."""
        if not self.gates:
            return ALLOWED
        if model is None and agent_id is not None:
            model = (
                await connection.execute(
                    sa.select(tables.agent.c.model).where(
                        tables.agent.c.id == agent_id, tables.agent.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one_or_none()
        ask = SpendAsk(
            workspace_id=workspace_id,
            moment=moment,
            agent_id=agent_id,
            turn_id=turn_id,
            model=model,
            self_funded=bool(
                await connection.scalar(
                    sa.select(self._self_funded(sa.literal(workspace_id), model))
                )
            ),
            member_admission=member_admission,
            intent=intent,
        )
        return composed(*[await gate.admit(connection, ask) for gate in self.gates])

    async def sustain(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        pending_micro_usd: int,
    ) -> SustainDecision:
        """Whether a running turn takes its next round, with its unbilled platform-paid spend
        priced in. A gate `absent` for the workspace is not asked."""
        for gate in self.gates:
            if gate.absent(workspace_id):
                continue
            held = await gate.sustain(connection, workspace_id, turn_id, pending_micro_usd)
            if held.outcome != ALLOW:
                return held
        return SUSTAINED

    def absent(self, workspace_id: UUID) -> bool:
        return all(gate.absent(workspace_id) for gate in self.gates)

    def admitting(self, workspace_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]:
        """Every gate's `admits_sql` over `workspace_id`, ANDed — what an owner-scope candidate
        read filters on, so a job whose work spends never opens a workspace its gates hold."""
        self_funded = self._self_funded(workspace_id, None)
        return sa.and_(
            sa.true(),
            *(
                predicate
                for gate in self.gates
                if (predicate := gate.admits_sql(workspace_id, self_funded)) is not None
            ),
        )

    def _self_funded(
        self, workspace_id: sa.ColumnElement[UUID], model: str | None
    ) -> sa.ColumnElement[bool]:
        """Correlates past every enclosing select, since auto-correlation reaches only the nearest
        one."""
        slot = None if model is None or self.key_slot_for is None else self.key_slot_for(model)
        slots = self.own_key_slots if model is None else () if slot is None else (slot,)
        if not slots:
            return sa.false()
        return sa.exists(
            sa.select(sa.literal(1))
            .where(
                tables.credential.c.workspace_id == workspace_id,
                tables.credential.c.slot.in_(slots),
            )
            .correlate_except(tables.credential)
        )


NO_SPEND_GATES = SpendGates()
