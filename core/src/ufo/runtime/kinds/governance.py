"""Governed agent-config change: a proposal is opened against a prompt digest and applied only if
that digest still matches at approval — a compare-and-swap, never a direct write to agent config."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import log
from ufo.schema import tables
from ufo.schema.records import APPROVED, PENDING, REJECTED, AgentChange, ProposalRef


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


@dataclass(frozen=True)
class Governance:
    """Workspace-scoped proposal lifecycle. `extension` names the proposer stamped on new
    proposals ("core" when core opens one); approval reads the proposer back from the row."""

    workspace_id: UUID
    extension: str

    async def propose_change(self, change: AgentChange) -> ProposalRef:
        proposal_id = uuid4()
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.id == change.agent_id,
                    )
                )
            ).one_or_none()
            if agent is None:
                raise ValueError(f"no agent {change.agent_id} in workspace")
            await connection.execute(
                sa.insert(tables.proposal).values(
                    id=proposal_id,
                    workspace_id=self.workspace_id,
                    agent_id=change.agent_id,
                    extension=self.extension,
                    from_digest=change.from_digest,
                    to_digest=prompt_digest(change.new_prompt),
                    body={"prompt": change.new_prompt},
                    status=PENDING,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return ProposalRef(proposal_id=proposal_id)

    async def approve_proposal(self, proposal_id: UUID) -> None:
        async with workspace_tx() as connection:
            proposal = (
                await connection.execute(
                    sa.select(
                        tables.proposal.c.agent_id,
                        tables.proposal.c.extension,
                        tables.proposal.c.from_digest,
                        tables.proposal.c.to_digest,
                        tables.proposal.c.body,
                        tables.proposal.c.status,
                    ).where(
                        tables.proposal.c.id == proposal_id,
                        tables.proposal.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
            if proposal is None:
                raise ValueError(f"no proposal {proposal_id}")
            if proposal.status != PENDING:
                raise ValueError(f"proposal {proposal_id} is {proposal.status}, not pending")
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt)
                    .where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.id == proposal.agent_id,
                    )
                    .with_for_update()
                )
            ).one()
            if proposal.from_digest != prompt_digest(agent.prompt):
                await connection.execute(
                    sa.update(tables.proposal)
                    .values(status=REJECTED, updated_at=sa.func.now())
                    .where(tables.proposal.c.id == proposal_id)
                )
                log("proposal.rejected", proposal_id=str(proposal_id), extension=proposal.extension)
                return
            await connection.execute(
                sa.update(tables.agent)
                .values(prompt=proposal.body["prompt"], updated_at=sa.func.now())
                .where(
                    tables.agent.c.workspace_id == self.workspace_id,
                    tables.agent.c.id == proposal.agent_id,
                )
            )
            await connection.execute(
                sa.update(tables.proposal)
                .values(status=APPROVED, updated_at=sa.func.now())
                .where(tables.proposal.c.id == proposal_id)
            )
        log(
            "proposal.approved",
            proposal_id=str(proposal_id),
            extension=proposal.extension,
            to_digest=proposal.to_digest,
        )
