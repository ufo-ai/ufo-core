"""Postgres custody of `onboard_claim` — the pre-tenant onboarding ledger, workspace-scoped rows in
the shared DB so the same RLS that polices tenants polices claims.

Copy-adapted from metalcraft's `onboard/store.py`: the same active-claim invariant (one live claim
per session ref, enforced by a partial unique index in the migration, never a read-then-write
check), the verification code held only as a hash. Adapted to run async over the extension's own
transaction rather than metalcraft's cross-tenant `system_tx`, and to carry the tenant it resolves
to across the poll-driven provisioning the async client streams."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext

_metadata = sa.MetaData()

onboard_claim = sa.Table(
    "onboard_claim",
    _metadata,
    sa.Column("claim_id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("email_domain", sa.Text, nullable=False),
    sa.Column("code_hash", sa.Text, nullable=False),
    sa.Column("surface", sa.Text, nullable=False),
    sa.Column("surface_ref", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("max_attempts", sa.Integer, nullable=False),
    sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("tenant_name", sa.Text, nullable=True),
    sa.Column("resulting_workspace_id", sa.Text, nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
    max_attempts: int
    verified_at: datetime | None
    tenant_name: str | None
    resulting_workspace_id: str | None
    completed_at: datetime | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OnboardStore:
    ext: ExtensionContext

    @property
    def workspace_id(self) -> UUID:
        return self.ext.store.workspace_id

    async def insert_claim(self, claim: OnboardClaim, now: datetime) -> None:
        async with self.ext.transaction() as connection:
            await connection.execute(
                sa.insert(onboard_claim).values(
                    claim_id=claim.claim_id,
                    workspace_id=self.workspace_id,
                    created_at=now,
                    updated_at=now,
                    email=claim.email,
                    email_domain=claim.email_domain,
                    code_hash=claim.code_hash,
                    surface=claim.surface,
                    surface_ref=claim.surface_ref,
                    expires_at=claim.expires_at,
                    attempts=claim.attempts,
                    max_attempts=claim.max_attempts,
                    verified_at=None,
                    tenant_name=None,
                    resulting_workspace_id=None,
                    completed_at=None,
                )
            )

    async def live_claim(self, surface: str, surface_ref: str) -> OnboardClaim | None:
        async with self.ext.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(onboard_claim)
                        .where(
                            onboard_claim.c.workspace_id == self.workspace_id,
                            onboard_claim.c.surface == surface,
                            onboard_claim.c.surface_ref == surface_ref,
                            onboard_claim.c.completed_at.is_(None),
                        )
                        .order_by(onboard_claim.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        claim_id = row["claim_id"]
        return OnboardClaim(
            claim_id=claim_id if isinstance(claim_id, UUID) else UUID(str(claim_id)),
            email=row["email"],
            email_domain=row["email_domain"],
            code_hash=row["code_hash"],
            surface=row["surface"],
            surface_ref=row["surface_ref"],
            expires_at=_aware(row["expires_at"]),  # type: ignore[arg-type]
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            verified_at=_aware(row["verified_at"]),
            tenant_name=row["tenant_name"],
            resulting_workspace_id=row["resulting_workspace_id"],
            completed_at=_aware(row["completed_at"]),
        )

    async def record_attempt(self, claim_id: UUID, attempts: int, now: datetime) -> None:
        await self._update(claim_id, {"attempts": attempts, "updated_at": now})

    async def mark_verified(self, claim_id: UUID, now: datetime) -> None:
        await self._update(claim_id, {"verified_at": now, "updated_at": now})

    async def start_provisioning(self, claim_id: UUID, tenant_name: str, now: datetime) -> None:
        await self._update(claim_id, {"tenant_name": tenant_name, "updated_at": now})

    async def complete(
        self, claim_id: UUID, tenant_name: str, resulting_workspace_id: str, now: datetime
    ) -> None:
        await self._update(
            claim_id,
            {
                "tenant_name": tenant_name,
                "resulting_workspace_id": resulting_workspace_id,
                "completed_at": now,
                "updated_at": now,
            },
        )

    async def _update(self, claim_id: UUID, values: dict[str, object]) -> None:
        async with self.ext.transaction() as connection:
            await connection.execute(
                sa.update(onboard_claim)
                .where(
                    onboard_claim.c.workspace_id == self.workspace_id,
                    onboard_claim.c.claim_id == claim_id,
                )
                .values(**values)
            )
