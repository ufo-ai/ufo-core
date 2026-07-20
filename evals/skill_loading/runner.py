"""Skill loading probed live and settled early: each case sends one natural task, watches the
conversation workspace for the `.skills/<name>/SKILL.md` files `load_skill` mounts as it
dispatches, and decides at the first observed mount — cancelling the still-running turn, so a case
costs the rounds before the load instead of the full task. The mount is durable workspace state,
so the verdict needs no terminal transcript and a cancelled turn grades the same as a finished
one; a turn that ends or stalls without the expected mount fails at its terminal or the deadline.
A turn that reaches its own terminal also records its response, calls, and redacted trajectory,
so the archive shows what the agent did instead of loading. A case whose expected skill is not
loadable under the active pack is excluded, not failed."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from typing import Protocol, cast
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import WorkspaceFile
from evals.harness.harness import EvalCaseResult, EvalReport, JsonObject, digest_payload
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import (
    CapabilityTarget,
    EvalConversations,
    capability_output,
    trajectory_snapshot,
)
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.ext.surface import workspace_key
from ufo.sandbox.session import WORKSPACE_DIR
from ufo.schema import tables
from ufo.schema.records import TurnStatus
from ufo.sdk.context import ExtensionContext, Trajectory
from ufo.skills.runtime import SKILL_MD, SKILLS_MOUNT_DIR

GRADER_REVISION = "expected-present-3"
LOAD_DEADLINE_SECONDS = 120.0
MOUNT_POLL_SECONDS = 0.5
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True)
class SkillLoadCase:
    """One natural member query whose first skill load must be `expected` — an exact registry
    name; loading a child mounts its parent too, so a parent name is satisfied by any of its
    children and a child's own parent must never be forbidden. `forbidden` names the plausible
    wrong picks — a forbidden mount fails the case only while the expected skill is absent.
    `workspace_files` stage the artifacts the query names, so the agent routes instead of asking
    for a missing file."""

    name: str
    message: str
    expected: str
    forbidden: tuple[str, ...] = ()
    workspace_files: tuple[WorkspaceFile, ...] = ()

    @property
    def grading(self) -> str:
        statement = f"skill {self.expected!r} mounts within {LOAD_DEADLINE_SECONDS:g}s"
        if self.forbidden:
            statement += f", never {', '.join(map(repr, self.forbidden))} without it"
        if any(name.startswith(f"{self.expected}/") for name in self.forbidden):
            statement += " (a forbidden child of it, never at all)"
        return statement

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "expected": self.expected,
            "forbidden": list(self.forbidden),
        }
        if self.workspace_files:
            payload["workspaceFiles"] = [
                {"path": item.path, "sha256": sha256(item.content).hexdigest()}
                for item in self.workspace_files
            ]
        return payload


@dataclass(frozen=True)
class SkillLoadObservation:
    """What the watcher saw at decision time: which watched skills were mounted, the turn's
    status, whether this run cancelled it, and how long the load took to appear."""

    mounted: tuple[str, ...]
    status: TurnStatus | None
    cancelled: bool
    elapsed_seconds: float


def skill_load_verdict(case: SkillLoadCase, observation: SkillLoadObservation) -> tuple[bool, str]:
    """Expected-present wins: a forbidden mount fails the case only when the expected skill has
    not mounted — one agentic round can load several skills at once, and a companion grabbed
    alongside the right pick is not a routing miss. The exception is a forbidden CHILD of the
    expected skill: its mount pulls the parent in with it, so the parent's presence is implied by
    the wrong pick rather than evidence of routing, and the case fails."""
    loaded_forbidden = [name for name in case.forbidden if name in observation.mounted]
    implied_by = [name for name in loaded_forbidden if name.startswith(f"{case.expected}/")]
    if implied_by:
        return False, (
            f"mounted forbidden child skill(s) of {case.expected!r}: {', '.join(implied_by)}"
        )
    if case.expected in observation.mounted:
        alongside = f" (alongside {', '.join(loaded_forbidden)})" if loaded_forbidden else ""
        return (
            True,
            f"mounted {case.expected!r} after {observation.elapsed_seconds:.1f}s{alongside}",
        )
    if loaded_forbidden:
        return False, (
            f"mounted forbidden skill(s) without {case.expected!r}: {', '.join(loaded_forbidden)}"
        )
    if observation.status in TERMINAL_STATUSES:
        return False, f"turn ended {observation.status} without loading {case.expected!r}"
    return False, (
        f"did not load {case.expected!r} within {LOAD_DEADLINE_SECONDS:g}s "
        f"(status {observation.status})"
    )


class TurnControl(Protocol):
    async def cancel(self, turn_id: UUID) -> bool: ...

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...


class SkillLoadRunTarget(Protocol):
    @property
    def ctx(self) -> ExtensionContext: ...

    @property
    def agent_id(self) -> UUID: ...

    @property
    def conversations(self) -> EvalConversations: ...

    @property
    def outcome(self) -> TurnControl: ...

    @property
    def blob(self) -> BlobStore | None: ...

    @property
    def loadable_skills(self) -> frozenset[str] | None: ...


def skill_loading_task(cases: tuple[SkillLoadCase, ...]) -> EvalTask:
    digest = digest_payload(
        {
            "runner": "skill-load-case",
            "task": "skill_loading",
            "grader": GRADER_REVISION,
            "deadlineSeconds": LOAD_DEADLINE_SECONDS,
            "cases": [case.payload() for case in cases],
        }
    )
    suite = SkillLoadingSuite(cases=cases, digest=digest)
    return EvalTask(
        "skill_loading",
        "skill_loading",
        digest,
        tuple(case.name for case in cases),
        suite.run,
    )


@dataclass(frozen=True)
class SkillLoadingSuite:
    cases: tuple[SkillLoadCase, ...]
    digest: str

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        run_target = cast(SkillLoadRunTarget, target)
        blob = run_target.blob
        if blob is None:
            raise RuntimeError("the skill_loading suite requires the workspace blob store")
        loadable = run_target.loadable_skills
        if loadable is None:
            raise RuntimeError("the skill_loading suite requires the pack's loadable-skill set")
        results = await gather_cases(
            slots,
            tuple(
                partial(self._gated_case, case, run_target, blob, loadable) for case in self.cases
            ),
        )
        return EvalReport(
            name="skill_loading", suite="skill_loading", digest=self.digest, cases=results
        )

    async def _gated_case(
        self,
        case: SkillLoadCase,
        target: SkillLoadRunTarget,
        blob: BlobStore,
        loadable: frozenset[str],
    ) -> EvalCaseResult:
        if case.expected not in loadable:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"skill {case.expected!r} is not loadable under the active pack",
                evidence=self._evidence(case, None, None),
                excluded=True,
            )
        return await self._case(case, target, blob)

    async def _case(
        self, case: SkillLoadCase, target: SkillLoadRunTarget, blob: BlobStore
    ) -> EvalCaseResult:
        conversation_id = await target.conversations.open(
            case.name, workspace_files=case.workspace_files
        )
        try:
            turn_id = await target.ctx.invoke(
                conversation_id, target.agent_id, case.message, f"{case.name}:{conversation_id}"
            )
        except Exception as error:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"invoke raised: {type(error).__name__}: {error}",
                evidence=self._evidence(case, None, conversation_id),
            )
        observation = await self._observe(case, target, blob, conversation_id, turn_id)
        passed, reason = skill_load_verdict(case, observation)
        evidence = self._evidence(case, observation, conversation_id)
        evidence["attempts"] = [
            await self._attempt(passed, reason, observation, target, conversation_id, turn_id)
        ]
        evidence["selectedAttempt"] = 0
        return EvalCaseResult(name=case.name, passed=passed, reason=reason, evidence=evidence)

    async def _observe(
        self,
        case: SkillLoadCase,
        target: SkillLoadRunTarget,
        blob: BlobStore,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> SkillLoadObservation:
        """Poll the watched mount keys and the turn row until a watched skill mounts, the turn
        reaches its own terminal, or the deadline fires — then end the turn if it still runs.
        Status reads before mounts, so a terminal status guarantees the mount set is final."""
        watch = (case.expected, *case.forbidden)
        clock = asyncio.get_running_loop().time
        started = clock()
        while True:
            async with workspace_tx() as connection:
                status = (
                    await connection.execute(
                        sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one_or_none()
            mounted = tuple(
                [name for name in watch if await blob.exists(_mount_key(conversation_id, name))]
            )
            elapsed = clock() - started
            terminal = status in TERMINAL_STATUSES
            if mounted or terminal or elapsed >= LOAD_DEADLINE_SECONDS:
                cancelled = False if terminal else await target.outcome.cancel(turn_id)
                return SkillLoadObservation(
                    mounted=mounted,
                    status=status,
                    cancelled=cancelled,
                    elapsed_seconds=elapsed,
                )
            await asyncio.sleep(MOUNT_POLL_SECONDS)

    def _evidence(
        self,
        case: SkillLoadCase,
        observation: SkillLoadObservation | None,
        conversation_id: UUID | None,
    ) -> JsonObject:
        evidence: JsonObject = {
            "message": case.message,
            "grading": case.grading,
            "expected": case.expected,
            "forbidden": list(case.forbidden),
            "workspaceFiles": [item.path for item in case.workspace_files],
            "conversationId": None if conversation_id is None else str(conversation_id),
        }
        if observation is not None:
            evidence["mounted"] = list(observation.mounted)
            evidence["status"] = observation.status
            evidence["cancelled"] = observation.cancelled
            evidence["elapsedSeconds"] = round(observation.elapsed_seconds, 1)
        return evidence

    async def _attempt(
        self,
        passed: bool,
        reason: str,
        observation: SkillLoadObservation,
        target: SkillLoadRunTarget,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> JsonObject:
        """The viewer-shaped record of the one run. A turn that reached its own terminal has a
        durable transcript, so its response, calls, and redacted trajectory record like a
        capability attempt; a turn this suite cancelled has none, and the mount evidence is the
        whole record."""
        attempt: JsonObject = {"passed": passed, "reason": reason, "response": "", "calls": []}
        if observation.cancelled:
            return attempt
        trajectory = await target.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            return attempt
        output = capability_output(trajectory.messages)
        snapshot = trajectory_snapshot(
            conversation_id, turn_id, observation.status, trajectory.messages
        )
        attempt["response"] = output.response
        attempt["calls"] = [
            {
                "name": call.name,
                "input": call.input,
                "result": call.result,
                "hasResult": call.has_result,
                "isError": call.is_error,
            }
            for call in output.calls
        ]
        attempt["trajectory"] = snapshot.model_dump(mode="json")
        return attempt


def _mount_key(conversation_id: UUID, skill: str) -> str:
    """The blob key of a mounted skill's `SKILL.md` — derived from the same runtime constants
    `mount_skill` writes through, so the watcher and the mounter cannot disagree."""
    rel = f"{SKILLS_MOUNT_DIR}/{skill}/{SKILL_MD}".removeprefix(f"{WORKSPACE_DIR}/")
    return workspace_key(conversation_id, rel)
