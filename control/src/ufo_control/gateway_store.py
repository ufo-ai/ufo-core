"""Postgres custody of the hosted onboarding claim ledger.

`create schema | table | index if not exists` is not atomic against a concurrent creator: two
gateway replicas booting together both find the object absent, both issue it, and the loser raises a
`pg_class`/`pg_namespace` unique violation that kills its startup. So every boot-time DDL statement
in this schema — this ledger's, the invite ledger's, the Slack Connect ledger's — runs inside a
transaction holding `BOOT_DDL_LOCK`. One key covers all three because they share the schema
statement, and the replica that waits then finds everything present and writes nothing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

SCHEMA = "ufo_control"
TABLE = f"{SCHEMA}.onboard_claim"
ACTIVE_INDEX = "onboard_claim_active_session"

BOOT_DDL_LOCK = f"select pg_advisory_xact_lock(hashtext('{SCHEMA} boot ddl'))"

DDL = (
    f"create schema if not exists {SCHEMA}",
    f"create table if not exists {TABLE} ("
    "  id uuid primary key,"
    "  email text not null,"
    "  email_domain text not null,"
    "  code_hash text not null,"
    "  surface text not null,"
    "  surface_ref text not null,"
    "  attempts integer not null default 0,"
    "  expires_at timestamptz not null,"
    "  verified_at timestamptz,"
    "  resulting_workspace_id text,"
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
    code_hash: str
    surface: str
    surface_ref: str
    expires_at: datetime
    attempts: int
    verified_at: datetime | None
    invite_id: UUID | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OnboardStore:
    pool: asyncpg.Pool

    async def ensure_table(self) -> None:
        """Create the claim ledger under `BOOT_DDL_LOCK` — one booting replica shapes at a time."""
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(BOOT_DDL_LOCK)
                for statement in DDL:
                    await connection.execute(statement)

    async def insert_claim(self, claim: OnboardClaim) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"insert into {TABLE} "
                "(id, email, email_domain, code_hash, surface, surface_ref, attempts, expires_at) "
                "values ($1, $2, $3, $4, $5, $6, $7, $8)",
                claim.claim_id,
                claim.email,
                claim.email_domain,
                claim.code_hash,
                claim.surface,
                claim.surface_ref,
                claim.attempts,
                claim.expires_at,
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
            code_hash=row["code_hash"],
            surface=row["surface"],
            surface_ref=row["surface_ref"],
            expires_at=_aware(row["expires_at"]),  # type: ignore[arg-type]
            attempts=int(row["attempts"]),
            verified_at=_aware(row["verified_at"]),
            invite_id=row["invite_id"],
        )

    async def record_attempt(self, claim_id: UUID, attempts: int) -> None:
        await self._update("attempts = $2", claim_id, attempts)

    async def mark_verified(self, claim_id: UUID) -> None:
        await self._update("verified_at = now()", claim_id)

    async def complete(self, claim_id: UUID, resulting_workspace_id: str) -> None:
        await self._update("resulting_workspace_id = $2", claim_id, resulting_workspace_id)

    async def delete_claim(self, claim_id: UUID) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(f"delete from {TABLE} where id = $1", claim_id)

    async def _update(self, assignment: str, claim_id: UUID, *values: object) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"update {TABLE} set {assignment} where id = $1", claim_id, *values
            )
