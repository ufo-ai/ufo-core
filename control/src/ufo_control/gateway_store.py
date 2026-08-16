"""Postgres custody of the hosted onboarding claim ledger. `ufo_control.schema` shapes `DDL`."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

SCHEMA = "ufo_control"
TABLE = f"{SCHEMA}.onboard_claim"
ACTIVE_INDEX = "onboard_claim_active_session"

DDL = (
    f"create table if not exists {TABLE} ("
    "  id uuid primary key,"
    "  email text not null,"
    "  email_domain text not null,"
    "  surface text not null,"
    "  surface_ref text not null,"
    "  expires_at timestamptz not null,"
    "  verified_at timestamptz,"
    "  resulting_workspace_id text,"
    "  created_workspace boolean not null default false,"
    "  invite_id uuid,"
    "  created_at timestamptz not null default now())",
    f"create unique index if not exists {ACTIVE_INDEX} on {TABLE} (surface, surface_ref)"
    "  where resulting_workspace_id is null",
)


@dataclass(frozen=True)
class OnboardClaim:
    claim_id: UUID
    email: str
    email_domain: str
    surface: str
    surface_ref: str
    expires_at: datetime
    verified_at: datetime | None
    invite_id: UUID | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OnboardStore:
    pool: asyncpg.Pool

    async def insert_claim(self, claim: OnboardClaim) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"insert into {TABLE} "
                "(id, email, email_domain, surface, surface_ref, expires_at, verified_at) "
                "values ($1, $2, $3, $4, $5, $6, $7)",
                claim.claim_id,
                claim.email,
                claim.email_domain,
                claim.surface,
                claim.surface_ref,
                claim.expires_at,
                claim.verified_at,
            )

    async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                f"select * from {TABLE} "
                "where surface = $1 and surface_ref = $2 and resulting_workspace_id is null",
                surface,
                surface_ref,
            )
        if row is None:
            return None
        return OnboardClaim(
            claim_id=row["id"],
            email=row["email"],
            email_domain=row["email_domain"],
            surface=row["surface"],
            surface_ref=row["surface_ref"],
            expires_at=_aware(row["expires_at"]),  # type: ignore[arg-type]
            verified_at=_aware(row["verified_at"]),
            invite_id=row["invite_id"],
        )

    async def mark_verified(self, claim_id: UUID) -> bool:
        async with self.pool.acquire() as connection:
            recorded = await connection.fetchval(
                f"update {TABLE} set verified_at = now() "
                "where id = $1 and verified_at is null returning true",
                claim_id,
            )
        return recorded is True

    async def complete(
        self, claim_id: UUID, resulting_workspace_id: str, *, created_workspace: bool
    ) -> None:
        """Whether this claim opened the workspace or joined one already there. A join is not a new
        customer — a contractor seated at their own email domain, or operator staff, resolves to a
        workspace their domain does not name — so the two cannot share one mark."""
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"update {TABLE} set resulting_workspace_id = $2, created_workspace = $3"
                " where id = $1",
                claim_id,
                resulting_workspace_id,
                created_workspace,
            )

    async def delete_claim(self, claim_id: UUID) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(f"delete from {TABLE} where id = $1", claim_id)

    async def delete_unverified_claim(self, claim_id: UUID) -> bool:
        async with self.pool.acquire() as connection:
            deleted = await connection.fetchval(
                f"delete from {TABLE} where id = $1 and verified_at is null returning true",
                claim_id,
            )
        return deleted is True
