"""Deploy routes and operator commands: an extension's reach for the deploy's own services and its
operator, and the `DeployContext` both run with.

A deploy route answers a service of the deploy — a sign-in gateway, a control plane — never a
member's browser, so it binds no workspace until its handler names one. A command is the same reach
from `ufoctl`. Both are core because the reads they need cross workspaces through the owner pool,
which no extension may open, and the seat they write runs `Provisioning`, whose founded handlers
only core may run. The points are privileged: first-party distributions only."""

from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request
from starlette.responses import Response

from ufo.blob import WorkspaceBlobStore
from ufo.db import owner_tx, workspace_tx
from ufo.harness.o11y import warn
from ufo.runtime.access.credentials import member_slot
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.member_profiles import MemberProfiles
from ufo.runtime.provisioning import Provisioned, Provisioning, Seating
from ufo.runtime.workspace import MEMBER_ROUTED_SLOTS, ws, ws_current
from ufo.schema import tables

CROSS_WORKSPACE_READ = "deploy.cross_workspace_read"
MEMBER_MODEL_PROVIDERS = tuple(sorted(MEMBER_ROUTED_SLOTS.values()))


class CommandRefused(Exception):
    """A command's refusal: `ufoctl` prints the message and exits 1."""


@dataclass(frozen=True)
class Membership:
    """One workspace an address is a member of, the address of that workspace's first member, and
    when this address joined it."""

    workspace_id: UUID
    first_email: str
    joined_at: datetime


@dataclass(frozen=True)
class FirstMember:
    workspace_id: UUID
    email: str


@dataclass(frozen=True)
class SeatedMember:
    workspace_id: UUID
    member_id: UUID
    email: str
    created_at: datetime


@dataclass(frozen=True)
class BoundDeploy:
    """A deploy route's hold on one workspace, bound for the life of `DeployContext.bound`."""

    profiles: MemberProfiles
    store: ScopedStore

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A workspace-scoped transaction, for the extension's own tables and the `ufo.sdk` rules
        that take a connection."""
        async with workspace_tx() as connection:
            yield connection

    async def put_member_model_key(self, member_id: UUID, provider: str, key: str) -> None:
        """Store a member's own model-provider key in the slot the connect flow writes."""
        slot = next(
            (slot for slot, served in MEMBER_ROUTED_SLOTS.items() if served == provider), None
        )
        if slot is None:
            raise ValueError(f"provider must be one of {list(MEMBER_MODEL_PROVIDERS)}")
        await ws_current().put_credential(member_slot(slot, member_id), key)


@dataclass(frozen=True)
class DeployContext:
    """What a deploy route or command reaches. Every read across workspaces takes a required
    `audit`: true logs `deploy.cross_workspace_read` naming the route and the extension, so an
    operator sees a read that leaves a workspace; false is for a sweep that repeats forever.
    `flag_backend` is the `[flags] backend` the deploy selects and `flag_keys` every flag a read in
    it consults, so a command writing a flag service refuses a deploy it does not serve and a key no
    code reads."""

    extension: str
    route: str
    blob: WorkspaceBlobStore
    provisioning: Provisioning
    flag_backend: str | None
    flag_keys: frozenset[str]

    async def provision(
        self,
        workspace_id: UUID,
        email: str,
        *,
        verify: Callable[[Seating], None] | None = None,
    ) -> Provisioned:
        """Found `workspace_id` when absent and seat `email` in it, in one transaction; a raising
        `verify` refuses the seat and writes nothing."""
        with ws(workspace_id):
            async with workspace_tx() as connection:
                return await self.provisioning.seat(connection, workspace_id, email, verify=verify)

    @asynccontextmanager
    async def bound(self, workspace_id: UUID) -> AsyncIterator[BoundDeploy]:
        """Bind `workspace_id` for the block: its member profiles, the extension's store in it,
        and its transactions."""
        with ws(workspace_id):
            yield BoundDeploy(
                profiles=MemberProfiles(workspace_id=workspace_id, blob=self.blob),
                store=ScopedStore(extension=self.extension),
            )

    async def memberships(self, email: str, *, audit: bool) -> tuple[Membership, ...]:
        """Every workspace holding a member row for `email`, seated or revoked, in joining order."""
        address = email.strip().lower()
        query = (
            sa.select(
                tables.member.c.workspace_id,
                _first_member_email(tables.member.c.workspace_id).label("email"),
                tables.member.c.created_at,
            )
            .where(tables.member.c.email == address)
            .order_by(tables.member.c.created_at, tables.member.c.workspace_id)
        )
        async with self.owner_transaction(audit=audit) as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            Membership(
                workspace_id=row.workspace_id, first_email=row.email, joined_at=row.created_at
            )
            for row in rows
        )

    async def workspaces_by_first_domain(
        self, domain: str, *, audit: bool
    ) -> tuple[FirstMember, ...]:
        """Every workspace whose first member's address is at `domain`, compared without case as
        `email_domain` reads a stored address."""
        first_email = _first_member_email(tables.workspace.c.id)
        query = (
            sa.select(tables.workspace.c.id.label("workspace_id"), first_email.label("email"))
            .where(
                sa.func.lower(first_email).endswith(f"@{domain.strip().lower()}", autoescape=True)
            )
            .order_by(tables.workspace.c.id)
        )
        async with self.owner_transaction(audit=audit) as connection:
            rows = (await connection.execute(query)).all()
        return tuple(FirstMember(workspace_id=row.workspace_id, email=row.email) for row in rows)

    async def first_member_emails(
        self, workspace_ids: Collection[UUID], *, audit: bool
    ) -> Mapping[UUID, str]:
        """The first member's address of each named workspace that has a member."""
        query = sa.select(
            tables.workspace.c.id.label("workspace_id"),
            _first_member_email(tables.workspace.c.id).label("email"),
        ).where(tables.workspace.c.id.in_(tuple(workspace_ids)))
        async with self.owner_transaction(audit=audit) as connection:
            rows = (await connection.execute(query)).all()
        return {row.workspace_id: row.email for row in rows if row.email is not None}

    async def workspace_count(self, *, audit: bool) -> int:
        async with self.owner_transaction(audit=audit) as connection:
            return (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
            ).scalar_one()

    async def workspace_ids(self, *, audit: bool) -> frozenset[UUID]:
        """Every workspace the deploy serves, with a member or without."""
        async with self.owner_transaction(audit=audit) as connection:
            return frozenset((await connection.execute(sa.select(tables.workspace.c.id))).scalars())

    async def seated_members(
        self, after: tuple[datetime, UUID] | None, limit: int, *, audit: bool
    ) -> tuple[SeatedMember, ...]:
        """One page of every seated member row across workspaces, ordered by (created_at, id) and
        strictly after `after`. The cursor orders one walk; it is not a mark to resume a later one
        from, since a row stamped earlier can commit after one stamped later."""
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        query = (
            sa.select(
                tables.member.c.workspace_id,
                tables.member.c.id,
                tables.member.c.email,
                tables.member.c.created_at,
            )
            .where(tables.member.c.seated_at.is_not(None))
            .order_by(tables.member.c.created_at, tables.member.c.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                sa.tuple_(tables.member.c.created_at, tables.member.c.id)
                > sa.tuple_(
                    sa.literal(after[0], sa.DateTime(timezone=True)),
                    sa.literal(after[1], sa.Uuid),
                )
            )
        async with self.owner_transaction(audit=audit) as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            SeatedMember(
                workspace_id=row.workspace_id,
                member_id=row.id,
                email=row.email,
                created_at=row.created_at,
            )
            for row in rows
        )

    @asynccontextmanager
    async def owner_transaction(self, *, audit: bool) -> AsyncIterator[AsyncConnection]:
        """An owner-pool transaction that pins no workspace, for the extension's own tables."""
        if audit:
            warn(CROSS_WORKSPACE_READ, route=self.route, extension=self.extension)
        async with owner_tx() as connection:
            yield connection


def _first_member_email(workspace_id: sa.ColumnElement[UUID]) -> sa.ScalarSelect[str]:
    first = tables.member.alias("first_member")
    return (
        sa.select(first.c.email)
        .where(first.c.workspace_id == workspace_id)
        .order_by(first.c.created_at, first.c.id)
        .limit(1)
        .correlate_except(first)
        .scalar_subquery()
    )


@dataclass(frozen=True)
class DeployRouteSpec:
    """An endpoint a deploy's own service calls, mounted at `/internal/<extension>/<path>` behind
    a constant-time check of `Authorization: Bearer <token>` against the manifest's
    `deploy_bearer_env`; any other request answers 401 before `handler` runs."""

    method: Literal["GET", "POST"]
    path: str
    handler: Callable[[DeployContext, Request], Awaitable[Response]]


@dataclass(frozen=True)
class CommandSpec[ParamsT: BaseModel]:
    """An operator verb, run as `ufoctl <extension> <name> --<field> <value>`: each field of
    `params` is one option, the validated model is passed to `run`, and `ufoctl` prints the line it
    returns. Raise `CommandRefused` to refuse with a message."""

    name: str
    help: str
    params: type[ParamsT]
    run: Callable[[DeployContext, ParamsT], Awaitable[str]]
