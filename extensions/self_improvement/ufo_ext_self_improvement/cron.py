"""The self-improvement loop's tick: batch-at-interval over the workspace's agents, never fired by
an event it caused. Per agent it opens at most one prompt candidate per prompt-version, replays +
grades the candidate's held-out set, and requires `stability_count` consecutive passing ticks before
opening a governed proposal — the only promotion path (a member approves it; nothing here writes the
agent). A candidate's stability count and its terminal suppression live in the extension's scoped
store, so a resolved candidate is never re-proposed until an approval moves the prompt digest."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from ufo.sdk.context import AgentChange, ExtensionContext, Trajectory
from ufo_ext_self_improvement.corpus import TaskExample, bad_trajectory, task_classes
from ufo_ext_self_improvement.evaluation import CandidateEvaluation
from ufo_ext_self_improvement.proposer import PromptProposer

STABILITY_COUNT = 2
CANDIDATE_KEY = "candidate:{agent_id}"

CandidateStatus = Literal["evaluating", "promoted", "rejected"]
EVALUATING: CandidateStatus = "evaluating"
PROMOTED: CandidateStatus = "promoted"
REJECTED: CandidateStatus = "rejected"


class CandidateState(BaseModel):
    """One agent's in-flight or resolved candidate, persisted in the scoped store. `from_digest`
    pins it to the prompt-version it targets; a terminal `status` suppresses re-proposal until an
    approval moves that digest."""

    from_digest: str
    prompt: str
    task: str
    held_out: tuple[str, ...]
    gate_passes: int = 0
    status: CandidateStatus = EVALUATING
    proposal_id: str | None = None


@dataclass(frozen=True)
class ImproveCron:
    ctx: ExtensionContext
    proposer: PromptProposer
    evaluation: CandidateEvaluation
    stability_count: int = STABILITY_COUNT

    async def run(self) -> None:
        for agent_id, trajectories in _by_agent(await self.ctx.trajectories()).items():
            await self._advance(agent_id, trajectories)

    async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None:
        from_digest = trajectories[0].agent_prompt_digest
        key = CANDIDATE_KEY.format(agent_id=agent_id)
        candidate = await self._active_or_open(key, from_digest, trajectories)
        if candidate is None:
            return
        await self._gate(agent_id, key, from_digest, candidate, trajectories)

    async def _active_or_open(
        self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]
    ) -> CandidateState | None:
        row = await self.ctx.store.get(key)
        if isinstance(row, dict):
            existing = CandidateState.model_validate(row)
            if existing.from_digest == from_digest:
                return existing if existing.status == EVALUATING else None
        classes = task_classes(trajectories)
        if not classes:
            return None
        proposal = await self.proposer.propose(trajectories[0].agent_prompt, classes[0])
        if proposal is None:
            return None
        opened = CandidateState(
            from_digest=from_digest,
            prompt=proposal.prompt,
            task=proposal.task,
            held_out=tuple(str(example.conversation_id) for example in classes[0].held_out),
        )
        await self.ctx.store.put(key, opened.model_dump(mode="json"))
        return opened

    async def _gate(
        self,
        agent_id: UUID,
        key: str,
        from_digest: str,
        candidate: CandidateState,
        trajectories: tuple[Trajectory, ...],
    ) -> None:
        global_held_out = tuple(
            example
            for task_class in task_classes(trajectories)
            if task_class.name != candidate.task
            for example in task_class.held_out
        )
        verdict = await self.evaluation.evaluate(
            candidate.prompt,
            trajectories[0].agent_prompt,
            _held_out(trajectories, candidate.held_out),
            global_held_out,
        )
        if not verdict.passed:
            await self._save(key, candidate, status=REJECTED, gate_passes=0)
            return
        passes = candidate.gate_passes + 1
        if passes < self.stability_count:
            await self._save(key, candidate, status=EVALUATING, gate_passes=passes)
            return
        ref = await self.ctx.propose_change(
            AgentChange(agent_id=agent_id, new_prompt=candidate.prompt, from_digest=from_digest)
        )
        await self._save(
            key, candidate, status=PROMOTED, gate_passes=passes, proposal_id=str(ref.proposal_id)
        )

    async def _save(
        self,
        key: str,
        candidate: CandidateState,
        *,
        status: CandidateStatus,
        gate_passes: int,
        proposal_id: str | None = None,
    ) -> None:
        updated = candidate.model_copy(
            update={"status": status, "gate_passes": gate_passes, "proposal_id": proposal_id}
        )
        await self.ctx.store.put(key, updated.model_dump(mode="json"))


def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]:
    grouped: dict[UUID, list[Trajectory]] = {}
    for trajectory in trajectories:
        grouped.setdefault(trajectory.agent_id, []).append(trajectory)
    return {agent_id: tuple(group) for agent_id, group in grouped.items()}


def _held_out(
    trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]
) -> tuple[TaskExample, ...]:
    by_id = {str(trajectory.conversation_id): trajectory for trajectory in trajectories}
    examples: list[TaskExample] = []
    for conversation_id in held_out:
        trajectory = by_id.get(conversation_id)
        if trajectory is None:
            continue
        flagged = bad_trajectory(trajectory)
        if flagged is not None:
            examples.append(flagged[1])
    return tuple(examples)
