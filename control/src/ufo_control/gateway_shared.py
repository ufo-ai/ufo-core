"""Resolve a verified email address to its shared-fleet workspaces."""

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
from ufo.seats import create_member, email_domain
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
class WorkspaceChoice:
    workspace_id: UUID
    label: str
    member: bool


@dataclass(frozen=True)
class EnsuredWorkspace:
    """The binding hosted onboarding just made: the workspace this member belongs to, and whether
    they administer it. The admin flag lets the concluding prompt offer billing management."""

    workspace_id: str
    admin: bool


@dataclass(frozen=True)
class SharedWorkspaces:
    """Resolve, create, and join shared-fleet workspaces for a verified address."""

    workspace_url: str
    pool: asyncpg.Pool

    async def choices(self, domain: str, email: str) -> tuple[WorkspaceChoice, ...]:
        """Every workspace this address may enter: its exact memberships plus the one its verified
        domain names. Membership grants only that workspace; a domain match grants its workspace."""
        member = email.strip().lower()
        normalized = domain.lower()
        deterministic = uuid5(NAMESPACE_DNS, normalized)
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                "with first_member as ("
                "  select distinct on (workspace_id) workspace_id, email"
                "  from member order by workspace_id, created_at, id), "
                "domain_workspace as ("
                "  select id as workspace_id from workspace where id = $2 "
                "  union "
                "  select workspace_id from first_member "
                "  where lower(split_part(email, '@', 2)) = $3) "
                "select matching.workspace_id, "
                "  lower(split_part(first_member.email, '@', 2)) as label, "
                "  false as domain_match, matching.created_at "
                "from member matching "
                "join first_member on first_member.workspace_id = matching.workspace_id "
                "where matching.email = $1 "
                "union all "
                "select workspace_id, $3 as label, true as domain_match, null as created_at "
                "from domain_workspace "
                "order by domain_match, created_at nulls last, workspace_id",
                member,
                deterministic,
                normalized,
            )
        domain_rows = [row for row in rows if row["domain_match"]]
        if len(domain_rows) > 1:
            raise RuntimeError(f"domain {normalized} maps to {len(domain_rows)} workspaces")
        member_ids = {row["workspace_id"] for row in rows if not row["domain_match"]}
        found = {
            row["workspace_id"]: row["label"] or str(row["workspace_id"])
            for row in rows
            if not row["domain_match"]
        }
        if domain_rows:
            found.setdefault(domain_rows[0]["workspace_id"], normalized)
        counts: dict[str, int] = {}
        for label in found.values():
            counts[label] = counts.get(label, 0) + 1
        return tuple(
            WorkspaceChoice(
                workspace_id=workspace_id,
                label=label if counts[label] == 1 else f"{label} ({str(workspace_id)[:8]})",
                member=workspace_id in member_ids,
            )
            for workspace_id, label in found.items()
        )

    async def create(self, domain: str, email: str) -> EnsuredWorkspace:
        """Create the workspace identified by this verified domain and seat its first member."""
        return await self._ensure(uuid5(NAMESPACE_DNS, domain.lower()), domain, email)

    async def join(self, choice: WorkspaceChoice, domain: str, email: str) -> EnsuredWorkspace:
        """Seat this verified address in one workspace its candidates authorized."""
        if not choice.member:
            return await self._ensure(choice.workspace_id, domain, email)
        member = email.strip().lower()
        with ws(choice.workspace_id):
            async with workspace_tx() as connection:
                admin = (
                    await connection.execute(
                        sa.select(tables.member.c.is_admin).where(
                            tables.member.c.workspace_id == choice.workspace_id,
                            tables.member.c.email == member,
                        )
                    )
                ).scalar_one_or_none()
        if admin is None:
            raise RuntimeError(f"{member} is no longer a member of {choice.workspace_id}")
        return EnsuredWorkspace(workspace_id=str(choice.workspace_id), admin=admin)

    async def _ensure(self, workspace_id: UUID, domain: str, email: str) -> EnsuredWorkspace:
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
                first_email = (
                    await connection.execute(
                        sa.select(tables.member.c.email)
                        .where(tables.member.c.workspace_id == workspace_id)
                        .order_by(tables.member.c.created_at, tables.member.c.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if first_email is not None and email_domain(first_email) != domain.lower():
                    raise RuntimeError(f"workspace {workspace_id} no longer belongs to {domain}")
                member_id = await create_member(
                    connection,
                    workspace_id,
                    member,
                    is_admin=first_email is None,
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
