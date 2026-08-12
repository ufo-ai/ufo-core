"""What a running turn mounted into its conversation workspace, watched until the answer is
settled. `load_skill` writes `.skills/<name>/SKILL.md` as it dispatches, so a routing verdict is
durable workspace state that needs neither the terminal transcript nor the rest of the task. The
watch ends at the first watched mount, the turn's own terminal, or the deadline, and ends a turn
still running — a case costs the rounds before the decision instead of the whole task. Status reads
before mounts, so a terminal status guarantees the mount set is final."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa

from evals.harness.target import EvalConversations
from ufo.db import workspace_tx
from ufo.sandbox.session import WORKSPACE_DIR
from ufo.schema import tables
from ufo.schema.records import TurnStatus
from ufo.sdk.context import Trajectory
from ufo.skills.runtime import SKILL_MD, SKILLS_MOUNT_DIR

MOUNT_POLL_SECONDS = 0.5
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True)
class MountObservation:
    """What the watcher saw at decision time: which watched skills were mounted, everything else
    the turn had mounted beside them, the turn's status, whether this run cancelled it, and how
    long the mount took to appear. `present` is the diagnosis a watch list cannot give — a probe
    that mounted nothing it was watching for names the skill that won the route instead."""

    mounted: tuple[str, ...]
    status: TurnStatus | None
    cancelled: bool
    elapsed_seconds: float
    present: tuple[str, ...] = ()


class TurnControl(Protocol):
    async def cancel(self, turn_id: UUID) -> bool: ...

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...


class MountWatchTarget(Protocol):
    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def outcome(self) -> TurnControl: ...


async def watch_mounts(
    target: MountWatchTarget,
    conversation_id: UUID,
    turn_id: UUID,
    names: tuple[str, ...],
    deadline_seconds: float,
    settle: tuple[str, ...] | None = None,
) -> MountObservation:
    """Watch `names`, and end the watch when one of `settle` mounts — every watched name by
    default. A caller that grades "this one, and not that one" settles on its own name alone: a
    skill mounts one `load_skill` call at a time, so a wrong pick followed a round later by the
    right one is a turn that corrected itself, and settling on the wrong pick would cancel the turn
    before the correction and grade a routing miss that did not happen."""
    settling = names if settle is None else settle
    clock = asyncio.get_running_loop().time
    started = clock()
    while True:
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        mounted = tuple(name for name in names if _mounted(target, conversation_id, name))
        elapsed = clock() - started
        terminal = status in TERMINAL_STATUSES
        if any(name in mounted for name in settling) or terminal or elapsed >= deadline_seconds:
            cancelled = False if terminal else await target.outcome.cancel(turn_id)
            return MountObservation(
                mounted=mounted,
                status=status,
                cancelled=cancelled,
                elapsed_seconds=elapsed,
                present=_present(target, conversation_id),
            )
        await asyncio.sleep(MOUNT_POLL_SECONDS)


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
