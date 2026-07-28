"""Resolve a verified organization domain to its shared-fleet workspace."""

import os
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import asyncpg
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from ufo.db import workspace_tx
from ufo.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.seats import create_member
from ufo.workspace import ws

SERVE_DSN_ENV = "UFO_CONTROL_SERVE_DSN"


def serve_dsn() -> str:
    dsn = os.environ.get(SERVE_DSN_ENV)
    if not dsn:
        raise RuntimeError(
            f"{SERVE_DSN_ENV} is unset — hosted onboarding writes the workspace row as the "
            "RLS-subject serve role"
        )
    return dsn


@dataclass(frozen=True)
class EnsuredWorkspace:
    """The binding hosted onboarding just made: the workspace this member belongs to, and whether
    they administer it. The admin flag lets the concluding prompt offer billing management."""

    workspace_id: str
    admin: bool


@dataclass(frozen=True)
class SharedWorkspaces:
    """Create or join the one workspace identified by a verified domain."""

    workspace_url: str
    pool: asyncpg.Pool

    async def exists(self, domain: str) -> bool:
        return await self._existing(domain) is not None

    async def ensure(self, domain: str, email: str) -> EnsuredWorkspace:
        workspace_id = await self._existing(domain) or uuid5(NAMESPACE_DNS, domain.lower())
        member = email.strip().lower()
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    insert(tables.workspace)
                    .values(id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now())
                    .on_conflict_do_nothing(index_elements=[tables.workspace.c.id])
                )
                await connection.execute(
                    sa.select(tables.workspace.c.id)
                    .where(tables.workspace.c.id == workspace_id)
                    .with_for_update()
                )
                first_member = not bool(
                    (
                        await connection.execute(
                            sa.select(tables.member.c.id)
                            .where(tables.member.c.workspace_id == workspace_id)
                            .limit(1)
                        )
                    ).one_or_none()
                )
                member_id = await create_member(
                    connection,
                    workspace_id,
                    member,
                    is_admin=first_member,
                )
                await connection.execute(
                    insert(tables.agent)
                    .values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name=DEFAULT_AGENT_NAME,
                        prompt=DEFAULT_AGENT_PROMPT,
                        model=DEFAULT_AGENT_MODEL,
                        is_main=True,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=[tables.agent.c.workspace_id, tables.agent.c.name]
                    )
                )
                admin = (
                    await connection.execute(
                        sa.select(tables.member.c.is_admin).where(tables.member.c.id == member_id)
                    )
                ).scalar_one()
        return EnsuredWorkspace(workspace_id=str(workspace_id), admin=admin)

    async def _existing(self, domain: str) -> UUID | None:
        normalized = domain.lower()
        deterministic = uuid5(NAMESPACE_DNS, normalized)
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                "with first_member as ("
                "  select distinct on (workspace_id) workspace_id, email"
                "  from member order by workspace_id, created_at, id) "
                "select id from workspace where id = $1 "
                "union "
                "select workspace_id as id from first_member "
                "where lower(split_part(email, '@', 2)) = $2 "
                "order by id",
                deterministic,
                normalized,
            )
        if len(rows) > 1:
            raise RuntimeError(f"domain {normalized} maps to {len(rows)} workspaces")
        return rows[0]["id"] if rows else None
