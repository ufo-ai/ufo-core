"""One-time invite codes gating new-workspace creation.

A verified email whose domain already has a workspace joins codeless; only the flow that creates a
workspace asks for a code. ``ufo-control invite <object-number>`` mints a code for a waitlist
object and prints the rendered invite email once; only the code's hash persists beside the claim
ledger in the platform schema. Redeeming consumes the code — ``consumed_at`` claimed under a row
lock while still null, so two concurrent flows can never both open a workspace on one code — and
stamps the claim's ``invite_id`` in the same transaction, so a crash can never leave a consumed
code detached from its claim. The consumption lands before the workspace write, and a resolution
retry proceeds without re-entering a consumed code."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import asyncpg

from ufo_control import gateway_store

TABLE = f"{gateway_store.SCHEMA}.invite_code"

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_GROUPS = 3
CODE_GROUP_LEN = 4
INVITE_TTL = timedelta(days=14)

DDL = (
    f"create table if not exists {TABLE} ("
    "  id uuid primary key,"
    "  code_hash text not null unique,"
    "  object_number integer not null check (object_number > 0),"
    "  expires_at timestamptz not null,"
    "  consumed_at timestamptz,"
    "  created_at timestamptz not null default now())",
    f"create unique index if not exists invite_code_live_object on {TABLE} (object_number)"
    "  where consumed_at is null",
)


class InviteError(RuntimeError):
    """Minting refused: the object is already identified or already holds a live code."""


def mint_code() -> str:
    return "-".join(
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUPS)
    )


def hash_invite(code: str) -> str:
    return sha256(code.strip().lower().encode()).hexdigest()


@dataclass(frozen=True)
class MintedInvite:
    code: str
    object_number: int
    expires_at: datetime


@dataclass(frozen=True)
class InviteUnknown:
    pass


@dataclass(frozen=True)
class InviteExpired:
    expires_at: datetime


@dataclass(frozen=True)
class InviteConsumed:
    pass


@dataclass(frozen=True)
class InviteAccepted:
    invite_id: UUID
    object_number: int
    consumed_at: datetime


@dataclass(frozen=True)
class InviteCodes:
    pool: asyncpg.Pool
    ttl: timedelta = INVITE_TTL

    async def mint(self, object_number: int) -> MintedInvite:
        code = mint_code()
        now = datetime.now(UTC)
        expires_at = now + self.ttl
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                standing = await connection.fetchrow(
                    f"select consumed_at, expires_at from {TABLE}"
                    " where object_number = $1 and (consumed_at is not null or expires_at > $2)"
                    " order by (consumed_at is not null) desc limit 1",
                    object_number,
                    now,
                )
                if standing is not None and standing["consumed_at"] is not None:
                    raise InviteError(f"object #{object_number} is already identified")
                if standing is not None:
                    expires = standing["expires_at"].astimezone(UTC).strftime("%Y-%m-%d %H:%M")
                    raise InviteError(
                        f"object #{object_number} already holds a live code, expires {expires} UTC"
                    )
                await connection.execute(
                    f"delete from {TABLE}"
                    " where object_number = $1 and consumed_at is null and expires_at <= $2",
                    object_number,
                    now,
                )
                try:
                    await connection.execute(
                        f"insert into {TABLE} (id, code_hash, object_number, expires_at) "
                        "values ($1, $2, $3, $4)",
                        uuid4(),
                        hash_invite(code),
                        object_number,
                        expires_at,
                    )
                except asyncpg.UniqueViolationError as raced:
                    raise InviteError(
                        f"object #{object_number} already holds a live code"
                    ) from raced
        return MintedInvite(code=code, object_number=object_number, expires_at=expires_at)

    async def redeem(
        self, code: str, claim_id: UUID
    ) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"select id, object_number, expires_at, consumed_at from {TABLE} "
                    "where code_hash = $1 for update",
                    hash_invite(code),
                )
                if row is None:
                    return InviteUnknown()
                if row["consumed_at"] is not None:
                    return InviteConsumed()
                if datetime.now(UTC) >= row["expires_at"]:
                    return InviteExpired(expires_at=row["expires_at"])
                consumed = await connection.fetchrow(
                    f"update {TABLE} set consumed_at = now() where id = $1 returning consumed_at",
                    row["id"],
                )
                await connection.execute(
                    f"update {gateway_store.TABLE} set invite_id = $2 where id = $1",
                    claim_id,
                    row["id"],
                )
        return InviteAccepted(
            invite_id=row["id"],
            object_number=row["object_number"],
            consumed_at=consumed["consumed_at"],
        )
