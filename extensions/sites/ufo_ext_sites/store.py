"""The hosted-site registry: the row a site's permanent link resolves through.

A site is `(workspace, conversation, name)` — the conversation that built it owns the name, so two
conversations may each serve a `dashboard` and neither takes the other's link. The row carries the
sandbox port its bytes come from, who created it, and the visibility every viewer is gated on;
re-deploying the same name updates the port and keeps whatever visibility the site already has, so
a member's choice in the frame survives the next deploy.

The registry is reached from two places that hold no common context — a tool handler through its
`ExtensionContext` and the frame through its `SurfaceContext` — so it takes the transaction and the
workspace it runs under as fields rather than deriving them from a turn that the frame does not
have. Scoping every query to that workspace is this module's job: the connection is a raw
whole-database one."""

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.audience import (
    FOREIGN_AUDIENCE_PREFIX,
    Audience,
    audience_member,
    parse_audience,
)

type Visibility = Literal["private", "workspace", "public"]
type Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]

VISIBILITY_LEVELS: tuple[Visibility, ...] = ("private", "workspace", "public")
SITE_VISIBILITY_GATE = "only the member who deployed a site may change who can open it"
PORT_HELD_BY_ANOTHER_MEMBER = (
    "that port is already serving a site another member deployed, and taking it over would unhost "
    "theirs: deploy under that site's name to update it, or ask them to unhost it"
)
PORT_UNHOST_NEEDS_A_SPEAKER = (
    "taking over that port would unhost the site already on it, which needs a live member: "
    "re-deploy under that site's name instead"
)
SITE_NAME_MAX = 48
_NAME_RUN = re.compile(r"[^a-z0-9]+")

_metadata = sa.MetaData()
hosted_site = sa.Table(
    "hosted_site",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("conversation_id", sa.Uuid, primary_key=True),
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("port", sa.Integer, nullable=False),
    sa.Column("visibility", sa.Text, nullable=False),
    sa.Column("creator_member_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


class NotTheSiteCreator(ValueError):
    """A deploy tried to re-gate or displace a site someone else created. Visibility is a disclosure
    decision with one owner and unhosting is a revocation, so re-deploying — under the same name or
    onto the same port — is not a second door to either. Surfaced to the model as a tool error,
    which is how the agent learns to ask the creator."""


class UnhostNeedsASpeaker(ValueError):
    """A deploy would have retired the site holding its port on a turn with no live member. Taking a
    port from a site is unhosting it, and a revocation needs a member who asked for it — never a
    background turn acting on someone's behalf."""


class InvalidSiteName(ValueError):
    """A site name slugged to nothing. The name is the site's identity, half its link, and its
    object name, so a name with no letters or digits is refused rather than stored — surfaced to the
    model as a tool error."""


def site_name(raw: str) -> str:
    """The stored name for a member-supplied one: lowercase, runs of anything else collapsed to a
    hyphen, bounded — the same grammar an object name obeys, so a registered site is addressable
    from chat as well as by link. The bound leaves room for the conversation digest the site's
    object name appends, which must itself stay inside the object-name limit."""
    slug = _NAME_RUN.sub("-", raw.lower()).strip("-")[:SITE_NAME_MAX].rstrip("-")
    if not slug:
        raise InvalidSiteName(f"site name {raw!r} has no letters or digits to name a site by")
    return slug


def default_visibility(audience: Audience) -> Visibility:
    """The visibility a new site takes from the conversation that produced it. A DM and a sealed
    external room both default private — an externally shared room must never default a site into
    the whole company — while an internal room and a workspace-shared conversation default to
    workspace."""
    parsed = parse_audience(audience)
    if audience_member(parsed) is not None or parsed.startswith(FOREIGN_AUDIENCE_PREFIX):
        return "private"
    return "workspace"


def visibility_level(value: str) -> Visibility:
    """One stored or submitted visibility value, refusing anything the column does not serve."""
    match value:
        case "private" | "workspace" | "public":
            return value
        case _:
            raise ValueError(
                f"site visibility {value!r} is not one of {', '.join(VISIBILITY_LEVELS)}"
            )


@dataclass(frozen=True)
class HostedSite:
    """One registered site: its conversation-scoped identity, the sandbox port serving it, who
    created it, and the visibility its frame gates every viewer on."""

    conversation_id: UUID
    name: str
    port: int
    visibility: Visibility
    creator_member_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class HostedSites:
    """One workspace's hosted sites, over the connection its caller already holds."""

    workspace_id: UUID
    transaction: Transaction

    async def register(
        self,
        conversation_id: UUID,
        name: str,
        port: int,
        creator_member_id: UUID,
        speaker_member_id: UUID | None,
        visibility: Visibility | None,
        audience: Audience,
    ) -> HostedSite:
        """Register the site a deploy just left running and return the row it resolves by. A port
        serves one origin, so any other site on this conversation's port is retired first —
        otherwise the older name would keep serving the newer deploy's bytes. That retire is an
        unhost, so it answers to the unhost rule rather than riding in behind a deploy: the port's
        current site must be the acting member's own, and a turn with no live speaker cannot take a
        port at all. An explicit
        `visibility` is written, and only its creator may write it: the column has one
        authorization rule and a re-deploy is not a way around it. Without one, an existing site
        keeps the visibility it has — so a teammate re-deploying never resets what the creator
        chose — and a new site takes the conversation audience's default. The name arrives already
        slugged by `site_name`, because its caller needs it to mint the link before it writes."""
        async with self.transaction() as connection:
            existing = await self._read(connection, conversation_id, name)
            if (
                existing is not None
                and visibility is not None
                and visibility != existing.visibility
                and existing.creator_member_id != creator_member_id
            ):
                raise NotTheSiteCreator(f"{SITE_VISIBILITY_GATE} ({name!r})")
            displaced = await self._on_port(connection, conversation_id, port, name)
            if displaced is not None:
                if displaced.creator_member_id != creator_member_id:
                    raise NotTheSiteCreator(f"{PORT_HELD_BY_ANOTHER_MEMBER} ({displaced.name!r})")
                if speaker_member_id is None:
                    raise UnhostNeedsASpeaker(f"{PORT_UNHOST_NEEDS_A_SPEAKER} ({displaced.name!r})")
                await connection.execute(
                    sa.delete(hosted_site).where(
                        hosted_site.c.workspace_id == self.workspace_id,
                        hosted_site.c.conversation_id == conversation_id,
                        hosted_site.c.name == displaced.name,
                    )
                )
            values: dict[str, object] = {"port": port, "updated_at": sa.func.now()}
            if visibility is not None:
                values["visibility"] = visibility
            updated = await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(**values)
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(hosted_site).values(
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        name=name,
                        port=port,
                        visibility=visibility or default_visibility(audience),
                        creator_member_id=creator_member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            registered = await self._read(connection, conversation_id, name)
        if registered is None:
            raise RuntimeError(f"site {name!r} vanished as it was registered")
        return registered

    async def read(self, conversation_id: UUID, name: str) -> HostedSite | None:
        async with self.transaction() as connection:
            return await self._read(connection, conversation_id, name)

    async def all(self) -> tuple[HostedSite, ...]:
        """Every site in the workspace, oldest first — the object kind's own gate filters them."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    self._columns()
                    .where(hosted_site.c.workspace_id == self.workspace_id)
                    .order_by(hosted_site.c.created_at, hosted_site.c.name)
                )
            ).all()
        return tuple(_site(row) for row in rows)

    async def set_visibility(
        self, conversation_id: UUID, name: str, visibility: Visibility
    ) -> HostedSite | None:
        """Move one site to a visibility level, returning the updated row, or None when the site is
        already gone."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(visibility=visibility, updated_at=sa.func.now())
            )
            return await self._read(connection, conversation_id, name)

    async def unregister(self, conversation_id: UUID, name: str) -> None:
        """Drop the site's registration: the link stops resolving. The sandbox keeps serving the
        port until its own lifecycle ends — hosting a site is registering a port, so unregistering
        it is unhosting."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.delete(hosted_site).where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
            )

    async def _on_port(
        self, connection: AsyncConnection, conversation_id: UUID, port: int, name: str
    ) -> HostedSite | None:
        """The site already serving this conversation's port under a different name, if any — at
        most one, since the registry holds the origin unique."""
        row = (
            await connection.execute(
                self._columns().where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.port == port,
                    hosted_site.c.name != name,
                )
            )
        ).one_or_none()
        return None if row is None else _site(row)

    async def _read(
        self, connection: AsyncConnection, conversation_id: UUID, name: str
    ) -> HostedSite | None:
        row = (
            await connection.execute(
                self._columns().where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
            )
        ).one_or_none()
        return None if row is None else _site(row)

    def _columns(self) -> sa.Select:
        return sa.select(
            hosted_site.c.conversation_id,
            hosted_site.c.name,
            hosted_site.c.port,
            hosted_site.c.visibility,
            hosted_site.c.creator_member_id,
            hosted_site.c.created_at,
            hosted_site.c.updated_at,
        )


def _site(row: sa.Row) -> HostedSite:
    return HostedSite(
        conversation_id=row.conversation_id,
        name=row.name,
        port=row.port,
        visibility=visibility_level(row.visibility),
        creator_member_id=row.creator_member_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
