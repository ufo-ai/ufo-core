"""Arc cases: one inbound, then every turn the system itself admits.

A capability case grades the turn it opened; an arc grades what happens *after* it ends — the turns
a scheduled fire or a completion hand-back admits on the same conversation once the opening turn is
terminal. So the runner opens one turn, lets the conversation go quiet, and hands the grader every
turn on it plus every child turn, each carrying the `admission_source` that caused it.

An arc grader is deterministic by construction. Its claims are about which turns exist, what caused
them, and what durable state they left — none of which a model judge can read, and all of which a
long-horizon objective's correctness is actually made of. `perturbation` is the world changing while
the agent is not looking: it lands after the opening turn is admitted, so only a woken turn can see
it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import WorkspaceFile, source_digest
from evals.harness.harness import EvalCaseResult, Json, JsonObject, infra_owned_fault
from evals.harness.target import CapabilityTarget
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import TerminalFrame

ACTIVITY_POLL_SECONDS = 2.0
NO_TRAJECTORY = "no trajectory"
"""The `openingStatus` of a run that left no turn row to read a status from — distinct from a
turn that reached one, which is why the record carries the word rather than an empty string."""


@dataclass(frozen=True)
class ArcTurn:
    """One turn the arc observed. `admission_source` is what caused it — `internal` for the arc's
    own opening invoke, `scheduled` for a due scheduled task, `member` for a resumed pause."""

    turn_id: UUID
    seq: int
    admission_source: str
    status: str
    inbound: str
    reply: str
    parent_turn_id: UUID | None
    subagent_profile: str | None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True)
class ArcObservation:
    """Everything an arc grader reads: the conversation's turns in seq order, every child turn a
    spawn produced, the tool names the opening turn called, and the workspace on disk."""

    conversation_id: UUID
    turns: tuple[ArcTurn, ...]
    children: tuple[ArcTurn, ...]
    opening_calls: tuple[str, ...]
    workspace_dir: Path

    @property
    def opening(self) -> ArcTurn:
        return self.turns[0]

    @property
    def woken(self) -> tuple[ArcTurn, ...]:
        """Every turn admitted after the opening turn — the arc's whole subject."""
        return tuple(turn for turn in self.turns if turn.seq > self.turns[0].seq)

    def woken_by(self, admission_source: str) -> tuple[ArcTurn, ...]:
        return tuple(turn for turn in self.woken if turn.admission_source == admission_source)

    def read_workspace(self, rel: str) -> str:
        path = self.workspace_dir / rel
        return path.read_text() if path.is_file() else ""


@dataclass(frozen=True)
class ArcVerdict:
    passed: bool
    reason: str


type ArcGrader = Callable[[ArcObservation], Awaitable[ArcVerdict]]
type ArcSeed = Callable[[UUID, UUID, UUID | None], Awaitable[None]]


@dataclass(frozen=True)
class ArcPerturbation:
    """A change to the world made `after_seconds` into the arc, once the opening turn is already
    admitted. It is what makes a wake worth grading: state the opening turn could not have seen."""

    after_seconds: float
    apply: Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class ArcCase:
    """`quiet_seconds` is how long the conversation must show no turn activity before the arc is
    read; `deadline_seconds` bounds the whole wait. Both are wall-clock: an arc runs against a live
    `ufoctl serve` whose scheduler fires on the real clock, so a case's heartbeat cadence and its
    quiet window are the same measurement. `min_seconds` is the floor under that — a conversation
    waiting on a wake is quiet by definition, so quiet alone would end the arc before the wake it
    exists to observe. Set it past the last fire a case expects.

    `member_key` binds the conversation to a workspace member by email. `seed` runs once the
    conversation exists and before the opening turn, receiving that member — the seam for state
    the arc's own opening turn cannot author. A scheduled task is the case in point: that turn is
    admitted `internal` and so carries no speaker, while creating a schedule requires a member
    requester, so an arc that asked the agent for one would measure that refusal instead of the
    wake."""

    name: str
    message: str
    grader: ArcGrader
    grading: str
    min_seconds: float = 0.0
    quiet_seconds: float = 30.0
    deadline_seconds: float = 300.0
    workspace_files: tuple[WorkspaceFile, ...] = ()
    member_key: str | None = None
    seed: ArcSeed | None = None
    perturbation: ArcPerturbation | None = None
    digest_tag: str = ""

    def payload(self) -> Json:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "grader": source_digest(self.grader),
            "grading": self.grading,
            "minSeconds": self.min_seconds,
            "quietSeconds": self.quiet_seconds,
            "deadlineSeconds": self.deadline_seconds,
            "workspaceFiles": [file.path for file in self.workspace_files],
        }
        if self.member_key is not None:
            payload["memberKey"] = self.member_key
        if self.seed is not None:
            payload["seed"] = source_digest(self.seed)
        if self.perturbation is not None:
            payload["perturbation"] = {
                "afterSeconds": self.perturbation.after_seconds,
                "apply": source_digest(self.perturbation.apply),
            }
        if self.digest_tag:
            payload["digestTag"] = self.digest_tag
        return payload


@dataclass(frozen=True)
class _Activity:
    """What the conversation and its children look like right now. Equality is the quiet test: an
    unchanged reading across the poll window with nothing live means the arc has settled."""

    turns: int
    latest: datetime | None
    live: int

    @property
    def settled(self) -> bool:
        return self.live == 0


@dataclass(frozen=True)
class ArcRun:
    case: ArcCase
    target: CapabilityTarget

    async def result(self) -> EvalCaseResult:
        conversation_id = await self.target.conversations.open(
            self.case.name, self.case.member_key, self.case.workspace_files
        )
        if self.case.seed is not None:
            await self.case.seed(
                ws_current().workspace_id, conversation_id, await self._member(conversation_id)
            )
        workspace_dir = self.target.conversations.workspace_path(conversation_id, "")
        perturbation = self._start_perturbation(workspace_dir)
        try:
            opening = await self.target.step(
                conversation_id, self.case.message, f"{self.case.name}:{conversation_id}"
            )
            if not opening.clean:
                status = opening.trajectory.status if opening.trajectory is not None else None
                return EvalCaseResult(
                    name=self.case.name,
                    passed=False,
                    reason=f"opening turn did not settle cleanly: {opening.failure_reason}",
                    # The status and error class the exclusion turned on. `turn produced no
                    # terminal transcript` is one reason over several faults — a wait the harness
                    # cancelled, a turn that terminalized empty, a run with no trajectory at all —
                    # and which one it was decides whether the case is the model's to answer for.
                    # Without them a red arc case reads only as the reason, and the archived record
                    # cannot say which fault the night hit.
                    evidence={
                        "grading": self.case.grading,
                        "openingStatus": status or NO_TRAJECTORY,
                        "openingErrorClass": opening.error_class or "",
                    },
                    excluded=infra_owned_fault(opening.error_class, opening.failure_reason, status),
                )
            await self._quiesce(conversation_id)
        finally:
            await self._stop_perturbation(perturbation)
        observation = await self._observe(
            conversation_id, tuple(opening.output.tools), workspace_dir
        )
        verdict = await self.case.grader(observation)
        return EvalCaseResult(
            name=self.case.name,
            passed=verdict.passed,
            reason=verdict.reason,
            evidence=self._evidence(observation),
        )

    async def _member(self, conversation_id: UUID) -> UUID | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one()

    def _start_perturbation(self, workspace_dir: Path) -> asyncio.Task[None] | None:
        if self.case.perturbation is None:
            return None
        perturbation = self.case.perturbation

        async def land() -> None:
            await asyncio.sleep(perturbation.after_seconds)
            await perturbation.apply(workspace_dir)

        return asyncio.create_task(land())

    async def _stop_perturbation(self, perturbation: asyncio.Task[None] | None) -> None:
        if perturbation is None:
            return
        if not perturbation.done():
            perturbation.cancel()
        try:
            await perturbation
        except asyncio.CancelledError:
            return

    async def _quiesce(self, conversation_id: UUID) -> None:
        started = monotonic()
        deadline = started + self.case.deadline_seconds
        seen = await self._activity(conversation_id)
        quiet_since = started
        while monotonic() < deadline:
            await asyncio.sleep(ACTIVITY_POLL_SECONDS)
            activity = await self._activity(conversation_id)
            if activity != seen:
                seen = activity
                quiet_since = monotonic()
                continue
            if monotonic() - started < self.case.min_seconds:
                continue
            if activity.settled and monotonic() - quiet_since >= self.case.quiet_seconds:
                return

    async def _activity(self, conversation_id: UUID) -> _Activity:
        async with workspace_tx() as connection:
            own = sa.select(tables.turn.c.id).where(
                tables.turn.c.conversation_id == conversation_id
            )
            scope = sa.or_(
                tables.turn.c.conversation_id == conversation_id,
                tables.turn.c.parent_turn_id.in_(own),
            )
            row = (
                await connection.execute(
                    sa.select(
                        sa.func.count().label("turns"),
                        sa.func.max(tables.turn.c.updated_at).label("latest"),
                        sa.func.coalesce(
                            sa.func.sum(sa.case((tables.turn.c.terminal.is_(None), 1), else_=0)),
                            0,
                        ).label("live"),
                    ).where(scope)
                )
            ).one()
        return _Activity(turns=row.turns, latest=row.latest, live=row.live)

    async def _observe(
        self, conversation_id: UUID, opening_calls: tuple[str, ...], workspace_dir: Path
    ) -> ArcObservation:
        async with workspace_tx() as connection:
            own = sa.select(tables.turn.c.id).where(
                tables.turn.c.conversation_id == conversation_id
            )
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.seq,
                        tables.turn.c.admission_source,
                        tables.turn.c.status,
                        tables.turn.c.inbound,
                        tables.turn.c.terminal,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.created_at,
                        tables.turn.c.updated_at,
                    )
                    .where(
                        sa.or_(
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.parent_turn_id.in_(own),
                        )
                    )
                    .order_by(tables.turn.c.created_at, tables.turn.c.seq)
                )
            ).all()
        turns = tuple(_arc_turn(row) for row in rows if row.conversation_id == conversation_id)
        children = tuple(_arc_turn(row) for row in rows if row.conversation_id != conversation_id)
        if not turns:
            raise RuntimeError(f"arc {self.case.name!r} observed no turn on its own conversation")
        return ArcObservation(
            conversation_id=conversation_id,
            turns=turns,
            children=children,
            opening_calls=opening_calls,
            workspace_dir=workspace_dir,
        )

    def _evidence(self, observation: ArcObservation) -> JsonObject:
        return {
            "grading": self.case.grading,
            "openingCalls": list(observation.opening_calls),
            "turns": [
                {
                    "seq": turn.seq,
                    "admissionSource": turn.admission_source,
                    "status": turn.status,
                    "reply": turn.reply[:400],
                }
                for turn in observation.turns
            ],
            "children": [
                {
                    "profile": turn.subagent_profile or "",
                    "status": turn.status,
                    "reply": turn.reply[:400],
                }
                for turn in observation.children
            ],
        }


def _arc_turn(row: sa.Row[tuple[object, ...]]) -> ArcTurn:
    terminal = TerminalFrame.model_validate(row.terminal) if row.terminal is not None else None
    return ArcTurn(
        turn_id=row.id,
        seq=row.seq,
        admission_source=row.admission_source,
        status=row.status,
        inbound=row.inbound,
        reply=terminal.text if terminal is not None else "",
        parent_turn_id=row.parent_turn_id,
        subagent_profile=row.subagent_profile,
        started_at=row.created_at,
        ended_at=row.updated_at if row.terminal is not None else None,
    )
