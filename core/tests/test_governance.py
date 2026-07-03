import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selfhost.db import workspace_tx
from selfhost.governance import Governance, prompt_digest
from selfhost.schema import tables
from selfhost.schema.records import AgentChange
from selfhost.surfaces.cli import router

BASE_PROMPT = "you are base"
SHARPER_PROMPT = "you are sharper"


@dataclass(frozen=True)
class Seed:
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    token: str


async def _seed(prompt: str) -> Seed:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    token = secrets.token_hex(16)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{token[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt=prompt,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface="cli",
                external_id=hashlib.sha256(token.encode()).hexdigest(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Seed(workspace_id, member_id, agent_id, token)


async def _agent_prompt(agent_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.prompt).where(tables.agent.c.id == agent_id)
            )
        ).scalar_one()


async def _proposal_row(proposal_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.proposal.c.status,
                    tables.proposal.c.from_digest,
                    tables.proposal.c.to_digest,
                    tables.proposal.c.body,
                    tables.proposal.c.extension,
                    tables.proposal.c.approved_by,
                ).where(tables.proposal.c.id == proposal_id)
            )
        ).one()


async def test_propose_writes_a_pending_proposal(db: None) -> None:
    seed = await _seed(BASE_PROMPT)
    governance = Governance(workspace_id=seed.workspace_id, extension="core")
    ref = await governance.propose_change(
        AgentChange(
            agent_id=seed.agent_id,
            new_prompt=SHARPER_PROMPT,
            from_digest=prompt_digest(BASE_PROMPT),
        )
    )
    row = await _proposal_row(ref.proposal_id)
    assert row.status == "pending"
    assert row.from_digest == prompt_digest(BASE_PROMPT)
    assert row.to_digest == prompt_digest(SHARPER_PROMPT)
    assert row.body == {"prompt": SHARPER_PROMPT}
    assert row.extension == "core"
    assert row.approved_by is None
    assert await _agent_prompt(seed.agent_id) == BASE_PROMPT


async def test_approve_applies_prompt_and_marks_approved(db: None) -> None:
    seed = await _seed(BASE_PROMPT)
    governance = Governance(workspace_id=seed.workspace_id, extension="core")
    ref = await governance.propose_change(
        AgentChange(
            agent_id=seed.agent_id,
            new_prompt=SHARPER_PROMPT,
            from_digest=prompt_digest(BASE_PROMPT),
        )
    )
    await governance.approve_proposal(ref.proposal_id, seed.member_id)
    assert await _agent_prompt(seed.agent_id) == SHARPER_PROMPT
    row = await _proposal_row(ref.proposal_id)
    assert row.status == "approved"
    assert row.approved_by == seed.member_id


async def test_stale_from_digest_is_rejected_and_prompt_unchanged(db: None) -> None:
    seed = await _seed(BASE_PROMPT)
    governance = Governance(workspace_id=seed.workspace_id, extension="core")
    ref = await governance.propose_change(
        AgentChange(
            agent_id=seed.agent_id,
            new_prompt=SHARPER_PROMPT,
            from_digest=prompt_digest(BASE_PROMPT),
        )
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(prompt="you moved on", updated_at=sa.func.now())
            .where(tables.agent.c.id == seed.agent_id)
        )
    await governance.approve_proposal(ref.proposal_id, seed.member_id)
    assert await _agent_prompt(seed.agent_id) == "you moved on"
    row = await _proposal_row(ref.proposal_id)
    assert row.status == "rejected"
    assert row.approved_by is None


async def test_cli_approve_endpoint_applies_the_change(db: None) -> None:
    seed = await _seed(BASE_PROMPT)
    governance = Governance(workspace_id=seed.workspace_id, extension="core")
    ref = await governance.propose_change(
        AgentChange(
            agent_id=seed.agent_id,
            new_prompt=SHARPER_PROMPT,
            from_digest=prompt_digest(BASE_PROMPT),
        )
    )
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://surface"
    ) as client:
        response = await client.post(
            f"/v1/proposals/{ref.proposal_id}/approve",
            headers={"authorization": f"Bearer {seed.token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "approved", "approved_by": str(seed.member_id)}
    assert await _agent_prompt(seed.agent_id) == SHARPER_PROMPT
