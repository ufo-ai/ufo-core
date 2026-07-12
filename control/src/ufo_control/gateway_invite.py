"""One-time invite codes gating new-workspace creation.

A verified email whose domain already has a workspace joins codeless; only the flow that creates a
workspace asks for a code. ``ufo-control invite`` prints the plaintext once; only its hash persists
beside the claim ledger in the platform schema. Redeeming burns the code — ``used_at`` claimed under
``used_at is null``, so two concurrent flows can never both open a workspace on one code — and
stamps the claim's ``invite_id`` in the same transaction, so a crash can never leave a burned code
detached from its claim. The burn lands before the workspace write, and a resolution retry proceeds
without re-entering a consumed code."""

import secrets
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import asyncpg

from ufo_control import gateway_store

TABLE = f"{gateway_store.SCHEMA}.invite_code"

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_GROUPS = 3
CODE_GROUP_LEN = 4

DDL = (
    f"create schema if not exists {gateway_store.SCHEMA}",
    f"create table if not exists {TABLE} ("
    "  id uuid primary key,"
    "  code_hash text not null unique,"
    "  used_at timestamptz,"
    "  created_at timestamptz not null default now())",
)


def mint_code() -> str:
    return "-".join(
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUPS)
    )


def hash_invite(code: str) -> str:
    return sha256(code.strip().lower().encode()).hexdigest()


@dataclass(frozen=True)
class InviteCodes:
    pool: asyncpg.Pool

    async def ensure_table(self) -> None:
        async with self.pool.acquire() as connection:
            for statement in DDL:
                await connection.execute(statement)

    async def mint(self) -> str:
        code = mint_code()
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"insert into {TABLE} (id, code_hash) values ($1, $2)",
                uuid4(),
                hash_invite(code),
            )
        return code

    async def redeem(self, code: str, claim_id: UUID) -> UUID | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"update {TABLE} set used_at = now() "
                    "where code_hash = $1 and used_at is null returning id",
                    hash_invite(code),
                )
                if row is None:
                    return None
                await connection.execute(
                    f"update {gateway_store.TABLE} set invite_id = $2 where id = $1",
                    claim_id,
                    row["id"],
                )
        return row["id"]
