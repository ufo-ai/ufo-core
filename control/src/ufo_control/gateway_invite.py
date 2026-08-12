"""One-time new-workspace invites, each a grant to one email domain.

A verified email whose domain already has a workspace joins ungranted; only the flow that creates a
workspace consults the ledger. ``ufo-control invite <object-number> <email>`` grants a waitlist
object's domain and emails it the invitation. Redeeming consumes the grant —
``consumed_at`` claimed under a row lock while still null, so two concurrent flows can never both
open a workspace on one grant — and stamps the claim's ``invite_id`` in the same transaction, so a
crash can never leave a consumed grant detached from its claim. The consumption lands before the
workspace write; repeated redemption by that claim is accepted.

A grant names a domain rather than travelling as a bearer secret. The member proves the granted
domain by verifying their own email, so the invitation carries nothing to retype, a forwarded
invitation reaches only the company it was issued to, and the colleague who actually runs the
installer is identified without a second grant."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg

from ufo_control import gateway_store
from ufo_control.gateway_email import WorkEmailPolicy, normalize_email

TABLE = f"{gateway_store.SCHEMA}.invite_code"

LIVE_OBJECT_INDEX = "invite_code_live_object"
LIVE_DOMAIN_INDEX = "invite_code_live_domain"
INVITE_TTL = timedelta(days=14)

DDL = (
    f"create table if not exists {TABLE} ("
    "  id uuid primary key,"
    "  object_number integer not null check (object_number > 0),"
    "  email text not null,"
    "  email_domain text not null,"
    "  expires_at timestamptz not null,"
    "  consumed_at timestamptz,"
    "  created_at timestamptz not null default now())",
    f"create unique index if not exists {LIVE_OBJECT_INDEX} on {TABLE} (object_number)"
    "  where consumed_at is null",
    f"create unique index if not exists {LIVE_DOMAIN_INDEX} on {TABLE} (email_domain)"
    "  where consumed_at is null",
)

STANDING = (
    f"select consumed_at, expires_at from {TABLE}"
    "  where {column} = $1 and (consumed_at is not null or expires_at > $2)"
    "  order by (consumed_at is not null) desc limit 1"
)


class InviteError(RuntimeError):
    """Granting refused: the object or the domain is already identified, or already holds a live
    grant."""


@dataclass(frozen=True)
class MintedInvite:
    object_number: int
    email: str
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

    async def available(self, email_domain: str) -> bool:
        """Whether this domain holds a live grant that can create its workspace."""
        async with self.pool.acquire() as connection:
            return bool(
                await connection.fetchval(
                    f"select 1 from {TABLE} "
                    "where email_domain = $1 and consumed_at is null and expires_at > now()",
                    email_domain,
                )
            )

    async def mint(self, object_number: int, email: str) -> MintedInvite:
        address, domain = normalize_email(email)
        WorkEmailPolicy().validate(address)
        now = datetime.now(UTC)
        expires_at = now + self.ttl
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._refuse_standing(
                    connection, "object_number", object_number, f"object #{object_number}", now
                )
                await self._refuse_standing(connection, "email_domain", domain, domain, now)
                await connection.execute(
                    f"delete from {TABLE}"
                    " where (object_number = $1 or email_domain = $2)"
                    "   and consumed_at is null and expires_at <= $3",
                    object_number,
                    domain,
                    now,
                )
                try:
                    await connection.execute(
                        f"insert into {TABLE}"
                        " (id, object_number, email, email_domain, expires_at)"
                        " values ($1, $2, $3, $4, $5)",
                        uuid4(),
                        object_number,
                        address,
                        domain,
                        expires_at,
                    )
                except asyncpg.UniqueViolationError as raced:
                    raise InviteError(
                        f"object #{object_number} or {domain} already holds a live invite"
                    ) from raced
        return MintedInvite(object_number=object_number, email=address, expires_at=expires_at)

    async def _refuse_standing(
        self,
        connection: asyncpg.Connection,
        column: str,
        value: object,
        subject: str,
        now: datetime,
    ) -> None:
        standing = await connection.fetchrow(STANDING.format(column=column), value, now)
        if standing is None:
            return
        if standing["consumed_at"] is not None:
            raise InviteError(f"{subject} is already identified")
        expires = standing["expires_at"].astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        raise InviteError(f"{subject} already holds a live invite, expires {expires} UTC")

    async def redeem(
        self, email_domain: str, claim_id: UUID
    ) -> InviteUnknown | InviteExpired | InviteConsumed | InviteAccepted:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"select id, object_number, expires_at, consumed_at from {TABLE} "
                    "where email_domain = $1 "
                    "order by (consumed_at is not null) desc, expires_at desc limit 1 "
                    "for update",
                    email_domain,
                )
                if row is None:
                    return InviteUnknown()
                if row["consumed_at"] is not None:
                    attached = await connection.fetchval(
                        f"select exists(select 1 from {gateway_store.TABLE} "
                        "where id = $1 and invite_id = $2)",
                        claim_id,
                        row["id"],
                    )
                    if attached:
                        return InviteAccepted(
                            invite_id=row["id"],
                            object_number=row["object_number"],
                            consumed_at=row["consumed_at"],
                        )
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
