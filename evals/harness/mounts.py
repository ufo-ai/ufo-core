"""What a running turn mounted into its conversation workspace, watched until the answer is
settled. `load_skill` writes `.skills/<name>/SKILL.md` as it dispatches, so a routing verdict is
durable workspace state that needs neither the terminal transcript nor the rest of the task. The
watch ends at the first watched mount, the turn's own terminal, or the deadline, and ends a turn
still running — a case costs the rounds before the decision instead of the whole task. Status reads
before mounts, so a terminal status guarantees the mount set is final.

The load deadline is charged from the turn's own work, not from admission. A shard runs its cases
against one box that also carries serve, its scheduler, and Postgres, so between admission and the
first round sit the queue, the dispatch claim, and the sandbox boot — latency the rig owns and the
agent cannot answer for. The turn's first durable engine step is where its work begins, so that is
where the clock starts; the wait before it is bounded separately by `START_DEADLINE_SECONDS` and a
turn that never starts is reported as such rather than graded as a routing failure."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import time
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa

from evals.harness.target import EvalConversations
from evals.harness.timing import TurnSteps
from ufo.db import workspace_tx
from ufo.sandbox.session import WORKSPACE_DIR
from ufo.schema import tables
from ufo.schema.records import TurnStatus
from ufo.sdk.context import Trajectory
from ufo.skills.runtime import SKILL_MD, SKILLS_MOUNT_DIR

MOUNT_POLL_SECONDS = 0.5
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
START_DEADLINE_SECONDS = 900.0
"""How long the watch waits for the turn to begin its own work. It bounds the rig's own latency —
queue wait behind the shard's other cases, the dispatch claim, and the sandbox boot — so it is
generous where the load deadline is tight: overrunning it says the stack is wedged, never that the
agent routed badly."""


@dataclass(frozen=True)
class MountObservation:
    """What the watcher saw at decision time: which watched skills were mounted, everything else
    the turn had mounted beside them, the turn's status, whether this run cancelled it, and how
    long the mount took to appear. `present` is the diagnosis a watch list cannot give — a probe
    that mounted nothing it was watching for names the skill that won the route instead.

    Three clocks, because they answer different questions. `elapsed_seconds` is the whole wall from
    admission, which is what the case cost the shard. `charged_seconds` is the turn's own work time,
    which is what the load deadline bounds and what a verdict may hold the agent to.
    `startup_seconds` is the rig's share between the two; `None` means the turn had not begun its
    own work when the watch ended, so nothing was measured of the agent at all."""

    mounted: tuple[str, ...]
    status: TurnStatus | None
    cancelled: bool
    elapsed_seconds: float
    present: tuple[str, ...] = ()
    charged_seconds: float = 0.0
    startup_seconds: float | None = None


class TurnControl(Protocol):
    async def cancel(self, turn_id: UUID) -> bool: ...

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...


class MountWatchTarget(Protocol):
    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def outcome(self) -> TurnControl: ...

    @property
    def turn_steps(self) -> TurnSteps: ...


async def watch_mounts(
    target: MountWatchTarget,
    conversation_id: UUID,
    turn_id: UUID,
    names: tuple[str, ...],
    deadline_seconds: float,
    settle: tuple[str, ...] | None = None,
    start_deadline_seconds: float = START_DEADLINE_SECONDS,
) -> MountObservation:
    """Watch `names`, and end the watch when one of `settle` mounts — every watched name by
    default. A caller that grades "this one, and not that one" settles on its own name alone: a
    skill mounts one `load_skill` call at a time, so a wrong pick followed a round later by the
    right one is a turn that corrected itself, and settling on the wrong pick would cancel the turn
    before the correction and grade a routing miss that did not happen.

    `deadline_seconds` runs against the turn's own work; `start_deadline_seconds` bounds the wait
    for that work to begin."""
    settling = names if settle is None else settle
    clock = asyncio.get_running_loop().time
    admitted = clock()
    working_from: float | None = None
    while True:
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        if working_from is None:
            found = await _work_started(target, turn_id)
            if found is not None:
                working_from = min(max(found, admitted), clock())
        mounted = tuple(name for name in names if _mounted(target, conversation_id, name))
        now = clock()
        charged = 0.0 if working_from is None else now - working_from
        expired = (
            now - admitted >= start_deadline_seconds
            if working_from is None
            else charged >= deadline_seconds
        )
        terminal = status in TERMINAL_STATUSES
        if any(name in mounted for name in settling) or terminal or expired:
            cancelled = False if terminal else await target.outcome.cancel(turn_id)
            return MountObservation(
                mounted=mounted,
                status=status,
                cancelled=cancelled,
                elapsed_seconds=clock() - admitted,
                present=_present(target, conversation_id),
                charged_seconds=charged,
                startup_seconds=None if working_from is None else working_from - admitted,
            )
        await asyncio.sleep(MOUNT_POLL_SECONDS)


def never_started(observation: MountObservation) -> bool:
    """Whether the watch ended with the turn's own work never begun — it sat in the queue or in
    setup for the whole start budget. No question reached the agent, so the case is the rig's fault
    and not a capability signal, the same line `infra_owned_fault` draws around an expired wait. A
    turn that reached a terminal status without starting is a wedge of ours and stays a failure."""
    return observation.startup_seconds is None and observation.status not in TERMINAL_STATUSES


async def _work_started(target: MountWatchTarget, turn_id: UUID) -> float | None:
    """The loop time the turn began its own work, read from the earliest durable engine step it
    recorded — the arrivals drain that opens the round loop, which the engine reaches only after the
    dispatch claim, the sandbox boot, and the preloaded mounts. `None` while no step is recorded
    yet."""
    steps = await target.turn_steps.steps(turn_id)
    starts = [step.started_at_epoch_ms for step in steps if step.started_at_epoch_ms is not None]
    if not starts:
        return None
    return asyncio.get_running_loop().time() - max(time() - min(starts) / 1000, 0.0)


def _mounted(target: MountWatchTarget, conversation_id: UUID, name: str) -> bool:
    relative = f"{SKILLS_MOUNT_DIR}/{name}/{SKILL_MD}".removeprefix(f"{WORKSPACE_DIR}/")
    return target.conversations.workspace_path(conversation_id, relative).exists()


def _present(target: MountWatchTarget, conversation_id: UUID) -> tuple[str, ...]:
    """Every skill mounted under the conversation, read once at decision time. A child skill mounts
    inside its parent's directory and names itself the way the registry does."""
    root = target.conversations.workspace_path(
        conversation_id, SKILLS_MOUNT_DIR.removeprefix(f"{WORKSPACE_DIR}/")
    )
    try:
        entries = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        return ()
    names: list[str] = []
    for entry in entries:
        if not (entry / SKILL_MD).is_file():
            continue
        names.append(entry.name)
        names.extend(
            f"{entry.name}/{child.name}"
            for child in sorted(entry.iterdir())
            if (child / SKILL_MD).is_file()
        )
    return tuple(names)
