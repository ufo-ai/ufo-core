"""The `enrichment_profile` table — one row per seated member, keyed by member — and the typed
shapes its JSON columns hold. `Profile` is the whole row past its keys: the kind's spec, what the
job writes, and what the hook reads, so the persisted and rendered shapes are one model.

Two smaller tables stand beside it. `enrichment_consent` holds what each member answered about
being looked up and the website they confirmed: nobody is enriched without a granted row, so a
member who never answered and a member who declined are both left alone, and the job reads the
confirmed website off the row it enriches by. `enrichment_backoff` holds how long one workspace's
job waits after a provider refusal, so a hard failure pauses the lookups rather than repeating them
every minute."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

MATCHED = "matched"
NO_MATCH = "no_match"
PDL_SOURCE = "pdl"
RECORDED_SOURCE = "recorded"
ProfileStatus = Literal["matched", "no_match"]
ProfileSource = Literal["pdl", "recorded"]

enrichment_profile = sa.Table(
    "enrichment_profile",
    sa.MetaData(),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, primary_key=True),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("website", sa.Text, nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("person", sa.JSON, nullable=True),
    sa.Column("company", sa.JSON, nullable=True),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
)

enrichment_consent = sa.Table(
    "enrichment_consent",
    sa.MetaData(),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, primary_key=True),
    sa.Column("granted", sa.Boolean, nullable=False),
    sa.Column("website", sa.Text, nullable=True),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
)

enrichment_backoff = sa.Table(
    "enrichment_backoff",
    sa.MetaData(),
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("retry_after", sa.DateTime(timezone=True), nullable=False),
)

BACKOFF_FIRST_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600

PROFILE_COLUMNS = (
    enrichment_profile.c.member_id,
    enrichment_profile.c.email,
    enrichment_profile.c.website,
    enrichment_profile.c.status,
    enrichment_profile.c.source,
    enrichment_profile.c.person,
    enrichment_profile.c.company,
    enrichment_profile.c.fetched_at,
)

agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("is_main", sa.Boolean),
)

member = sa.table(
    "member",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("email", sa.Text),
    sa.column("seated_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


class Person(BaseModel):
    """What the provider said about the member behind the sign-up address."""

    model_config = ConfigDict(extra="forbid")
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    job_title_role: str | None = None
    job_title_levels: tuple[str, ...] = ()
    job_company_name: str | None = None
    job_company_website: str | None = None
    linkedin_url: str | None = None
    location_name: str | None = None
    likelihood: int | None = None


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    locality: str | None = None
    region: str | None = None
    country: str | None = None


class Company(BaseModel):
    """What the provider said about the company behind a website."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    display_name: str | None = None
    website: str | None = None
    industry: str | None = None
    size: str | None = None
    employee_count: int | None = None
    founded: int | None = None
    summary: str | None = None
    location: Location | None = None
    linkedin_url: str | None = None
    likelihood: int | None = None


class Profile(BaseModel):
    """One member's enrichment: the address it was looked up by, the website the member confirmed
    (None until they do, or when they cleared it), whether anything matched, which provider
    answered, and what it said."""

    model_config = ConfigDict(extra="forbid")
    email: str
    website: str | None
    status: ProfileStatus
    source: ProfileSource
    person: Person | None
    company: Company | None
    fetched_at: datetime


@dataclass(frozen=True)
class SeatedMember:
    member_id: UUID
    email: str
    website: str | None = None


@dataclass(frozen=True)
class StoredProfile:
    member_id: UUID
    profile: Profile


def due_workspaces() -> sa.Select[tuple[UUID]]:
    """The candidate select: workspaces holding a seated member who agreed to be looked up, has no
    profile row yet, and whose workspace is not waiting out a provider refusal."""
    consented = sa.exists().where(
        enrichment_consent.c.member_id == member.c.id,
        enrichment_consent.c.granted.is_(True),
    )
    paused = sa.exists().where(
        enrichment_backoff.c.workspace_id == member.c.workspace_id,
        enrichment_backoff.c.retry_after > datetime.now(UTC),
    )
    return (
        sa.select(member.c.workspace_id)
        .where(member.c.seated_at.is_not(None), consented, ~_has_profile(), ~paused)
        .distinct()
    )


def _has_profile() -> sa.ColumnElement[bool]:
    return sa.exists().where(enrichment_profile.c.member_id == member.c.id)


@dataclass(frozen=True)
class Profiles:
    """Reads and writes over one workspace's rows, on the caller's transaction."""

    connection: AsyncConnection
    workspace_id: UUID

    async def due(self, limit: int) -> tuple[SeatedMember, ...]:
        """The members who agreed and hold no row yet, each with the website they confirmed."""
        rows = (
            await self.connection.execute(
                sa.select(member.c.id, member.c.email, enrichment_consent.c.website)
                .join_from(
                    member,
                    enrichment_consent,
                    enrichment_consent.c.member_id == member.c.id,
                )
                .where(
                    member.c.workspace_id == self.workspace_id,
                    member.c.seated_at.is_not(None),
                    enrichment_consent.c.granted.is_(True),
                    ~_has_profile(),
                )
                .order_by(member.c.created_at, member.c.id)
                .limit(limit)
            )
        ).all()
        return tuple(
            SeatedMember(member_id=row.id, email=row.email, website=row.website) for row in rows
        )

    async def seated(self, member_id: UUID) -> SeatedMember | None:
        row = (
            await self.connection.execute(
                sa.select(member.c.id, member.c.email).where(
                    member.c.workspace_id == self.workspace_id,
                    member.c.id == member_id,
                    member.c.seated_at.is_not(None),
                )
            )
        ).one_or_none()
        return None if row is None else SeatedMember(member_id=row.id, email=row.email)

    async def write(self, member_id: UUID, profile: Profile) -> None:
        values = {
            "email": profile.email,
            "website": profile.website,
            "status": profile.status,
            "source": profile.source,
            "person": None if profile.person is None else profile.person.model_dump(mode="json"),
            "company": None if profile.company is None else profile.company.model_dump(mode="json"),
            "fetched_at": profile.fetched_at,
        }
        updated = await self.connection.execute(
            sa.update(enrichment_profile)
            .values(**values)
            .where(
                enrichment_profile.c.workspace_id == self.workspace_id,
                enrichment_profile.c.member_id == member_id,
            )
        )
        if updated.rowcount == 0:
            await self.connection.execute(
                sa.insert(enrichment_profile).values(
                    workspace_id=self.workspace_id, member_id=member_id, **values
                )
            )

    async def forget(self, member_id: UUID) -> None:
        await self.connection.execute(
            sa.delete(enrichment_profile).where(
                enrichment_profile.c.workspace_id == self.workspace_id,
                enrichment_profile.c.member_id == member_id,
            )
        )

    async def rows(self, limit: int) -> tuple[StoredProfile, ...]:
        rows = (
            await self.connection.execute(
                sa.select(*PROFILE_COLUMNS)
                .where(enrichment_profile.c.workspace_id == self.workspace_id)
                .order_by(enrichment_profile.c.fetched_at, enrichment_profile.c.member_id)
                .limit(limit)
            )
        ).all()
        return tuple(_stored(row) for row in rows)

    async def one(self, member_id: UUID) -> StoredProfile | None:
        row = (
            await self.connection.execute(
                sa.select(*PROFILE_COLUMNS).where(
                    enrichment_profile.c.workspace_id == self.workspace_id,
                    enrichment_profile.c.member_id == member_id,
                )
            )
        ).one_or_none()
        return None if row is None else _stored(row)

    async def by_email(self, email: str) -> StoredProfile | None:
        row = (
            await self.connection.execute(
                sa.select(*PROFILE_COLUMNS).where(
                    enrichment_profile.c.workspace_id == self.workspace_id,
                    sa.func.lower(enrichment_profile.c.email) == email.strip().lower(),
                )
            )
        ).one_or_none()
        return None if row is None else _stored(row)


@dataclass(frozen=True)
class Consents:
    """What each member answered about being looked up, on the caller's transaction. A member with
    no row answered nothing, which is not agreement."""

    connection: AsyncConnection
    workspace_id: UUID

    async def record(self, member_id: UUID, *, granted: bool, website: str | None = None) -> None:
        values = {"granted": granted, "website": website, "decided_at": datetime.now(UTC)}
        updated = await self.connection.execute(
            sa.update(enrichment_consent)
            .values(**values)
            .where(
                enrichment_consent.c.workspace_id == self.workspace_id,
                enrichment_consent.c.member_id == member_id,
            )
        )
        if updated.rowcount == 0:
            await self.connection.execute(
                sa.insert(enrichment_consent).values(
                    workspace_id=self.workspace_id, member_id=member_id, **values
                )
            )


@dataclass(frozen=True)
class Backoff:
    """How long this workspace's job waits after a provider refusal: the delay the provider asked
    for, or a doubling one from a minute to an hour. A tick that enriched somebody clears it, so a
    workspace comes back on its own rather than stopping."""

    connection: AsyncConnection
    workspace_id: UUID

    async def pause(self, retry_after: float | None) -> float:
        attempts = (
            await self.connection.execute(
                sa.select(enrichment_backoff.c.attempts).where(
                    enrichment_backoff.c.workspace_id == self.workspace_id
                )
            )
        ).scalar_one_or_none() or 0
        seconds = (
            min(BACKOFF_FIRST_SECONDS * 2**attempts, BACKOFF_MAX_SECONDS)
            if retry_after is None
            else min(retry_after, BACKOFF_MAX_SECONDS)
        )
        values = {
            "attempts": attempts + 1,
            "retry_after": datetime.now(UTC) + timedelta(seconds=seconds),
        }
        updated = await self.connection.execute(
            sa.update(enrichment_backoff)
            .values(**values)
            .where(enrichment_backoff.c.workspace_id == self.workspace_id)
        )
        if updated.rowcount == 0:
            await self.connection.execute(
                sa.insert(enrichment_backoff).values(workspace_id=self.workspace_id, **values)
            )
        return seconds

    async def clear(self) -> None:
        await self.connection.execute(
            sa.delete(enrichment_backoff).where(
                enrichment_backoff.c.workspace_id == self.workspace_id
            )
        )


async def agent_is_main(connection: AsyncConnection, workspace_id: UUID, agent_id: UUID) -> bool:
    """Whether the bound agent is the workspace's main one, the way the core member kind narrows
    its own portal page."""
    return bool(
        (
            await connection.execute(
                sa.select(agent.c.is_main).where(
                    agent.c.workspace_id == workspace_id, agent.c.id == agent_id
                )
            )
        ).scalar_one_or_none()
    )


def _stored(row: sa.Row) -> StoredProfile:
    return StoredProfile(
        member_id=row.member_id,
        profile=Profile(
            email=row.email,
            website=row.website,
            status=row.status,
            source=row.source,
            person=None if row.person is None else Person.model_validate(row.person),
            company=None if row.company is None else Company.model_validate(row.company),
            fetched_at=(
                row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=UTC)
            ),
        ),
    )
