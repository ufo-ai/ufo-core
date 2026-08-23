"""The hosted-site registry: the row a site's permanent link resolves through.

A site is `(workspace, conversation, name)` — the conversation that built it owns the name, so two
conversations may each serve a `dashboard` and neither takes the other's link. The row carries the
sandbox port its bytes come from, who created it, the visibility every viewer is gated on, the
picture of the page its last deploy captured, and — for a static deploy — the manifest of the
source promoted into the blob store, which is what the ingress serves and what outlives the
sandbox; re-deploying the same name updates the port and keeps whatever visibility the site
already has, so a member's choice in the frame survives the next deploy.

The registry is reached from two places that hold no common context — a tool handler through its
`ExtensionContext` and the frame through its `SurfaceContext` — so it takes the transaction and the
workspace it runs under as fields rather than deriving them from a turn that the frame does not
have. Scoping every query to that workspace is this module's job: the connection is a raw
whole-database one."""

import re
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.audience import (
    FOREIGN_AUDIENCE_PREFIX,
    Audience,
    audience_member,
    parse_audience,
)
from ufo.sdk.sandbox import ContainmentError, contained_relative
from ufo.sdk.tools import StoredPreview

type Visibility = Literal["private", "workspace", "public"]
type Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]

VISIBILITY_LEVELS: tuple[Visibility, ...] = ("private", "workspace", "public")
SITE_VISIBILITY_GATE = "only the member who deployed a site may change who can open it"
PORT_HELD_BY_ANOTHER_MEMBER = (
    "that port is already serving a site another member deployed, and taking it over would unhost "
    "theirs: deploy under that site's name to update it, or ask them to unhost it"
)
SITE_NAME_MAX = 48
SOURCE_PATH_ANCHOR = "/site-source"
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
    sa.Column("generation", sa.Uuid, nullable=False, default=uuid4),
    sa.Column("deploy_generation", sa.BigInteger, nullable=False),
    sa.Column("homepage_agent_id", sa.Uuid, nullable=True),
    sa.Column("preview_blob_key", sa.Text, nullable=True),
    sa.Column("preview_size_bytes", sa.Integer, nullable=True),
    sa.Column("share_card_blob_key", sa.Text, nullable=True),
    sa.Column("share_card_hash", sa.Text, nullable=True),
    sa.Column("source_manifest", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


class SiteFile(BaseModel):
    """One file of a site's stored source: its size, the media type it serves as, and the digest of
    its bytes — the ETag a read answers with."""

    size: int
    media_type: str
    sha256: str


class SourceManifest(BaseModel):
    """The stored source a static deploy promoted into the blob store: the workspace-relative key
    prefix its files live under, and every file by its site-relative path. The prefix carries a
    per-deploy token, so each deploy's keys are immutable and every deploy's prefix stands in the
    store — the site's stored history. A row with no manifest serves off its sandbox port instead —
    a published app with its own server."""

    root: str
    files: dict[str, SiteFile]

    @field_validator("root")
    @classmethod
    def _rooted(cls, root: str) -> str:
        if not root.startswith(("sites/", "apps/")) or not root.endswith("/"):
            raise ValueError(f"source root {root!r} must be a sites/ or apps/ key prefix")
        return root

    @field_validator("files")
    @classmethod
    def _pathed(cls, files: dict[str, SiteFile]) -> dict[str, SiteFile]:
        """Every path must already be the plain relative form the guard's lexical tier resolves it
        to — a path that normalizes to something else (a leading slash, a dot segment, an escape)
        is refused rather than rewritten, since the manifest's keys are what a read is answered
        by."""
        for path in files:
            refusal = f"source path {path!r} is not a plain site-relative path"
            if path.startswith("/") or "\\" in path or any(char < " " for char in path):
                raise ValueError(refusal)
            try:
                resolved = contained_relative(path, SOURCE_PATH_ANCHOR)
            except ContainmentError as escape:
                raise ValueError(refusal) from escape
            if resolved != f"{SOURCE_PATH_ANCHOR}/{path}":
                raise ValueError(refusal)
        return files


PORT_UNHOST_NEEDS_A_SPEAKER = (
    "taking over that port would unhost the site already on it, and only the member can ask for "
    "that: report what you built and leave the site up"
)


class UnhostNeedsASpeaker(ValueError):
    """A deploy would have retired the site holding its port on a turn with no live speaker. Taking
    a port from a site is unhosting it, and neither a scheduled fire nor a subagent acting on
    someone's behalf is that member asking — so the conversation keeps the site it has. The message
    stops at that: telling the refused turn to re-deploy under the standing site's name would name
    an act that succeeds, since a same-name deploy displaces nothing, and repoint a live link at a
    build nobody asked to put there."""


class NotTheSiteCreator(ValueError):
    """A deploy tried to re-gate or displace a site someone else created. Visibility is a disclosure
    decision with one owner and unhosting is a revocation, so re-deploying — under the same name or
    onto the same port — is not a second door to either. Surfaced to the model as a tool error,
    which is how the agent learns to ask the creator."""


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
    created it, the visibility its frame gates every viewer on, the picture of the page its last
    deploy captured — the blob key and exact size a signed preview link is minted over — and the
    share card composed from that page, whose digest is the only part of it a public URL carries."""

    conversation_id: UUID
    name: str
    port: int
    visibility: Visibility
    creator_member_id: UUID
    generation: UUID
    deploy_generation: int
    homepage_agent_id: UUID | None
    preview_blob_key: str | None
    preview_size_bytes: int | None
    share_card_blob_key: str | None
    share_card_hash: str | None
    source_manifest: str | None
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
        visibility: Visibility | None,
        audience: Audience,
        may_unhost: bool,
        *,
        manifest: str | None,
    ) -> HostedSite:
        """Register the site a deploy just left running and return the row it resolves by. A port
        serves one origin, so any other site on this conversation's port is retired first —
        otherwise the older name would keep serving the newer deploy's bytes. That retire is an
        unhost, so it answers to the unhost rule rather than riding in behind a deploy: the port's
        current site must be the acting member's own. An explicit
        `visibility` is written, and only its creator may write it: the column has one
        authorization rule and a re-deploy is not a way around it. Without one, an existing site
        keeps the visibility it has — so a teammate re-deploying never resets what the creator
        chose — and a new site takes the conversation audience's default. The name arrives already
        slugged by `site_name`, because its caller needs it to mint the link before it writes.

        The preview columns are left as they are: the picture is taken after this write and lands
        through `set_preview`, so a re-deploy keeps the picture the site already had until its own
        render answers.

        `deploy_generation` is a clock stamp held strictly above whatever the row already carries,
        never a counter: a counter dies with its row, so a name unhosted and deployed again would
        repeat values its earlier life used — and the portal, which remounts the homepage frame when
        this value changes, would keep showing the old bytes at the same URL.

        `manifest` is the stored-source manifest a static deploy promoted, or None for a deploy
        serving off its sandbox port — written either way, so the column always states how the
        current deploy serves and a publish over a former static deploy sheds the stale source."""
        stamp = time.time_ns() // 1_000
        async with self.transaction() as connection:
            displaced = await self._refuse(
                connection, conversation_id, name, port, creator_member_id, visibility, may_unhost
            )
            if displaced is not None:
                await connection.execute(
                    sa.delete(hosted_site).where(
                        hosted_site.c.workspace_id == self.workspace_id,
                        hosted_site.c.conversation_id == conversation_id,
                        hosted_site.c.name == displaced.name,
                    )
                )
            values: dict[str, object] = {
                "port": port,
                "source_manifest": manifest,
                "updated_at": sa.func.now(),
                "deploy_generation": sa.case(
                    (
                        hosted_site.c.deploy_generation >= stamp,
                        hosted_site.c.deploy_generation + 1,
                    ),
                    else_=stamp,
                ),
            }
            if visibility is not None:
                values["visibility"] = visibility
                if existing := await self._read(connection, conversation_id, name):
                    if existing.visibility != visibility:
                        values["generation"] = uuid4()
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
                        generation=uuid4(),
                        deploy_generation=stamp,
                        source_manifest=manifest,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            registered = await self._read(connection, conversation_id, name)
        if registered is None:
            raise RuntimeError(f"site {name!r} vanished as it was registered")
        return registered

    async def redeploy(self, conversation_id: UUID, name: str, manifest: str) -> HostedSite | None:
        """Replace one site's stored source in place, returning the updated row, or None when the
        site is already gone. Nothing else moves — port, creator, visibility and binding stay — so
        a deploy directed from another conversation updates the very page the member's link opens
        rather than founding a fork. The `deploy_generation` stamp answers to the same
        strictly-above rule as `register`'s, which is what remounts the portal's frame."""
        stamp = time.time_ns() // 1_000
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(
                    source_manifest=manifest,
                    updated_at=sa.func.now(),
                    deploy_generation=sa.case(
                        (
                            hosted_site.c.deploy_generation >= stamp,
                            hosted_site.c.deploy_generation + 1,
                        ),
                        else_=stamp,
                    ),
                )
            )
            return await self._read(connection, conversation_id, name)

    async def homepage(self, agent_id: UUID) -> HostedSite | None:
        """The one site bound as this agent's homepage, or None — at most one, held by the partial
        unique index."""
        async with self.transaction() as connection:
            row = (
                await connection.execute(
                    self._columns().where(
                        hosted_site.c.workspace_id == self.workspace_id,
                        hosted_site.c.homepage_agent_id == agent_id,
                    )
                )
            ).one_or_none()
        return None if row is None else _site(row)

    async def set_preview(self, conversation_id: UUID, name: str, preview: StoredPreview) -> None:
        """Write the picture a deploy photographed of its page onto the site's row.

        Its own write, after `register`: the render is what a deploy can end inside, and the row is
        what decides which name serves the port, so the registration cannot wait behind a picture.
        A site unhosted or renamed off the port while the shot was drawing matches nothing here and
        keeps the picture it had, which is what a failed render leaves too."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(
                    preview_blob_key=preview.blob_key,
                    preview_size_bytes=preview.size_bytes,
                    updated_at=sa.func.now(),
                )
            )

    async def set_share_card(
        self, conversation_id: UUID, name: str, blob_key: str, digest: str
    ) -> None:
        """Write the share card composed from the site's page onto its row, with the digest of the
        card's own bytes.

        The digest is what the card's public URL carries, so a redeploy addresses a new URL and no
        crawler's cache has to be invalidated, and a URL naming a digest the row no longer holds is
        a 404. Its own write, after the registration and the picture, matching nothing for a site
        unhosted or renamed off the port while the card was drawing: that site keeps the card it
        had, which is what a failed render leaves too."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(
                    share_card_blob_key=blob_key,
                    share_card_hash=digest,
                    updated_at=sa.func.now(),
                )
            )

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

    async def conversation(
        self, conversation_id: UUID, names: tuple[str, ...], limit: int
    ) -> tuple[HostedSite, ...]:
        """This conversation's hosted sites, oldest first."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    self._columns()
                    .where(
                        hosted_site.c.workspace_id == self.workspace_id,
                        hosted_site.c.conversation_id == conversation_id,
                        hosted_site.c.name.in_(names),
                    )
                    .order_by(hosted_site.c.created_at, hosted_site.c.name)
                    .limit(limit)
                )
            ).all()
        return tuple(_site(row) for row in rows)

    async def visible_conversation(
        self,
        conversation_id: UUID,
        member_id: UUID,
        limit: int,
        *,
        admin: bool,
        homepage_agents: frozenset[UUID],
    ) -> tuple[HostedSite, ...]:
        """This conversation's sites the member may open: their own, any no longer private, any
        whose binding follows a workspace-visible agent (`homepage_agents`), and — for an admin —
        all of them, since a private site answers its creator and workspace admins."""
        gate = sa.or_(
            hosted_site.c.creator_member_id == member_id,
            hosted_site.c.visibility != "private",
            hosted_site.c.homepage_agent_id.in_(homepage_agents),
        )
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    self._columns()
                    .where(
                        hosted_site.c.workspace_id == self.workspace_id,
                        hosted_site.c.conversation_id == conversation_id,
                        *(() if admin else (gate,)),
                    )
                    .order_by(hosted_site.c.created_at, hosted_site.c.name)
                    .limit(limit)
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
                .values(visibility=visibility, generation=uuid4(), updated_at=sa.func.now())
            )
            return await self._read(connection, conversation_id, name)

    async def set_homepage(
        self, agent_id: UUID, conversation_id: UUID, name: str
    ) -> HostedSite | None:
        """Bind the named site as the agent's homepage, returning the bound row, or None when the
        site is already gone. The agent's previous binding clears in the same transaction, so the
        partial unique index holds at most one homepage per agent. The row's own visibility is
        untouched: a bound site answers to the agent's visibility, and the column resumes on
        unbind."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.homepage_agent_id == agent_id,
                )
                .values(homepage_agent_id=None)
            )
            await connection.execute(
                sa.update(hosted_site)
                .where(
                    hosted_site.c.workspace_id == self.workspace_id,
                    hosted_site.c.conversation_id == conversation_id,
                    hosted_site.c.name == name,
                )
                .values(homepage_agent_id=agent_id)
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

    async def refuse_or_pass(
        self,
        conversation_id: UUID,
        name: str,
        port: int,
        creator_member_id: UUID,
        visibility: Visibility | None,
        may_unhost: bool,
    ) -> HostedSite | None:
        """Raise whatever `register` would raise for these arguments, writing nothing, and answer
        the site this registration would displace — the caller's to unhost where its own serve has
        already killed that site's server.

        A deploy serves before it registers, and serving kills whatever holds the port — the
        member's own site, in a container their turns share. So the caller asks here first, while
        that site is still up, and `register` asks again inside the write: one set of refusals, one
        answer, checked where a refusal is free and enforced where the row is decided."""
        async with self.transaction() as connection:
            return await self._refuse(
                connection, conversation_id, name, port, creator_member_id, visibility, may_unhost
            )

    async def _refuse(
        self,
        connection: AsyncConnection,
        conversation_id: UUID,
        name: str,
        port: int,
        creator_member_id: UUID,
        visibility: Visibility | None,
        may_unhost: bool,
    ) -> HostedSite | None:
        """Every refusal a registration can raise, and the site this one would displace.

        Retiring the site on the port is an unhost, so it answers to the unhost rule rather than
        riding in behind a deploy: the port's current site must be the acting member's own, and the
        turn must be one that may unhost."""
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
            if not may_unhost:
                raise UnhostNeedsASpeaker(f"{PORT_UNHOST_NEEDS_A_SPEAKER} ({displaced.name!r})")
        return displaced

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
            hosted_site.c.generation,
            hosted_site.c.deploy_generation,
            hosted_site.c.homepage_agent_id,
            hosted_site.c.preview_blob_key,
            hosted_site.c.preview_size_bytes,
            hosted_site.c.share_card_blob_key,
            hosted_site.c.share_card_hash,
            hosted_site.c.source_manifest,
            hosted_site.c.created_at,
            hosted_site.c.updated_at,
        )


def _aware(moment: datetime) -> datetime:
    """The stored UTC instant as an aware datetime — sqlite hands the column back naive, and a
    reader comparing it against a turn's aware stamp must never guess the zone."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _site(row: sa.Row) -> HostedSite:
    return HostedSite(
        conversation_id=row.conversation_id,
        name=row.name,
        port=row.port,
        visibility=visibility_level(row.visibility),
        creator_member_id=row.creator_member_id,
        generation=row.generation,
        deploy_generation=row.deploy_generation,
        homepage_agent_id=row.homepage_agent_id,
        preview_blob_key=row.preview_blob_key,
        preview_size_bytes=row.preview_size_bytes,
        share_card_blob_key=row.share_card_blob_key,
        share_card_hash=row.share_card_hash,
        source_manifest=row.source_manifest,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )
