"""The `site` object kind: hosted sites as workspace objects, so chat reaches the same visibility
column the in-frame selector writes.

One object per registered site, named `<site-name>-<conversation-digest>` (`dashboard-9f21c0a4e3b7`,
carried in the deploy result), because two conversations may each host a `dashboard` and each keeps
its own link. A site belongs to the member who deployed it: its creator sees it whatever its
visibility, workspace admins see every site, every other member sees it once it is no longer
private, and only the creator may change who can open it — a workspace admin may narrow a site to
private but never widen one. A site bound as an agent's homepage answers to the agent instead: its
effective visibility is the agent's, its own column lies dormant until unbind, and an apply naming
another level is refused toward the agent object. It is the agent's page rather than a site the
workspace shares, so it is disclosed at the agent's level and never browsed: it answers the agent's
own audience by name — the edit flow any of them may direct starts with that read — while unhosting
stays with its creator and workspace admins, and it stands in a listing only where the read names
its binding. Create is refused naming `deploy_website`: a
site exists by serving a port, and only a deploy knows which port. Delete unregisters it, and the
link stops resolving."""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    ConversationObjectGrant,
    GeneratedObjectOwner,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    OwnedRow,
    VerbNotSupported,
    owner_emails,
)
from ufo.sdk.tools import ToolContext
from ufo_ext_sites.share_card import draw_from_stored_shot
from ufo_ext_sites.source import materialize_source
from ufo_ext_sites.store import (
    SITE_VISIBILITY_GATE,
    HostedSite,
    HostedSites,
    Visibility,
    visibility_level,
)
from ufo_ext_sites.surface import site_url

SITE_KIND = "site"
SITES_ARE_DEPLOYED = (
    "sites exist only by deploying one — build the site in the workspace and deploy_website it"
)
VISIBILITY_GATE = f"{SITE_VISIBILITY_GATE}; a workspace admin may only make it private"
UNHOST_GATE = "only the member who deployed a site, or a workspace admin, may unhost it"
HOMEPAGE_FOLLOWS_AGENT = (
    "this site is an agent's homepage: its visibility follows the agent, so change the agent "
    "object's visibility instead"
)
HOMEPAGE_FIELD = "homepage_agent"
HOMEPAGE_MINE = "mine"
CONVERSATION_DIGEST_HEX = 12


class SiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Visibility = Field(
        description=(
            "Who may open the site's link: private (its creator and workspace admins), workspace "
            "(any member of the workspace, signed in), or public (anyone holding the link). A "
            "site bound as an agent's homepage follows the agent's visibility instead."
        )
    )


def site_object_name(conversation_id: UUID, name: str) -> str:
    """The object name for one site's identity: its own name plus a digest of the conversation that
    hosts it, so the same site name in two conversations addresses two objects."""
    digest = hashlib.sha256(str(conversation_id).encode()).hexdigest()[:CONVERSATION_DIGEST_HEX]
    return f"{name}-{digest}"


def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None:
    digest = hashlib.sha256(str(conversation_id).encode()).hexdigest()[:CONVERSATION_DIGEST_HEX]
    suffix = f"-{digest}"
    if not object_name.endswith(suffix):
        return None
    name = object_name.removesuffix(suffix)
    return name if site_object_name(conversation_id, name) == object_name else None


def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]:
    return {site_object_name(site.conversation_id, site.name): site for site in sites}


def _workspace(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("the site kind reads through its ExtensionContext")
    return ext


def _sites(ext: ExtensionContext | None) -> HostedSites:
    scoped = _workspace(ext)
    return HostedSites(scoped.store.workspace_id, scoped.transaction)


def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility:
    """The level a viewer is actually gated on: the agent's visibility for a homepage-bound site —
    indexed, not defaulted, because a binding naming an absent agent is a broken row, never a
    quieter one — and the site's own column otherwise."""
    if site.homepage_agent_id is None:
        return site.visibility
    return visibility_level(agents[site.homepage_agent_id])


def _summary(site: HostedSite, visibility: Visibility) -> str:
    return f"{site.name} · {visibility} · sandbox port {site.port}"


def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None:
    """The signed link to the picture this site's last deploy captured, or None for a site whose
    deploy captured none — one standing since before the capture, or one whose render failed. The
    link carries the same raster grant a shared file's picture is served through, so the portal
    draws it with no route of its own."""
    if site.preview_blob_key is None or site.preview_size_bytes is None:
        return None
    return scoped.image_preview_url(site.preview_blob_key, site.preview_size_bytes)


@dataclass(frozen=True)
class SiteObjects(MemberReadableObjects[SiteSpec, GeneratedObjectOwner]):
    """Read, re-gate, and unhost handlers over the workspace's registered sites. Ownership is the
    site's creator and disclosure is its own `visibility` column — the gate the frame enforces per
    visit, read here through the one registry both surfaces write.

    A homepage-bound row is not a site the workspace shares: the page is the agent's, disclosed by
    the agent object and gated per visit on the agent's visibility, so the row carries the agent's
    disclosure rather than its own and is never browsed. It answers the agent's audience by name,
    unhosting stays with its creator and workspace admins, and it stands in a listing only where
    the read names its binding."""

    kind_name: ClassVar[str] = SITE_KIND
    mutate_gate: ClassVar[str] = VISIBILITY_GATE
    delete_gate: ClassVar[str] = UNHOST_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool:
        return old.visibility != "private" and spec.visibility == "private"

    def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool:
        return HOMEPAGE_FIELD not in row.fields or HOMEPAGE_FIELD in query.filters

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        """`homepage_agent` takes `mine` beside an agent id: nothing tells a turn its own agent
        id, so the sentinel is how an agent addresses its own binding, resolved here where the
        turn is in hand — the same viewer-relative reading the `mine` row field carries
        elsewhere."""
        if query.filters.get(HOMEPAGE_FIELD) == HOMEPAGE_MINE:
            query = replace(
                query,
                filters={**query.filters, HOMEPAGE_FIELD: str(ctx.turn.agent_id)},
            )
        return await super().list(ctx, query)

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        scoped = _workspace(ext)
        base = scoped.public_base_url
        named = _named(await _sites(ext).all())
        agents = await scoped.agent_visibilities()
        emails = await owner_emails(site.creator_member_id for site in named.values())
        return tuple(
            OwnedRow(
                name=name,
                summary=_summary(site, (effective := effective_visibility(site, agents))),
                owner=GeneratedObjectOwner(
                    member_id=site.creator_member_id,
                    audience=(
                        conversation_audience(site.creator_member_id)
                        if effective == "private"
                        else SHARED_AUDIENCE
                    ),
                    generation=site.generation,
                ),
                fields={
                    "conversation": str(site.conversation_id),
                    "created_at": site.created_at.isoformat(),
                    "visibility": effective_visibility(site, agents),
                    "owner_email": emails.get(site.creator_member_id),
                    "mine": site.creator_member_id == member_id,
                    "deploy_generation": site.deploy_generation,
                }
                | (
                    {}
                    if not base
                    else {
                        "site_url": site_url(
                            base, scoped.store.workspace_id, site.conversation_id, site.name
                        )
                    }
                )
                | (
                    {}
                    if site.homepage_agent_id is None
                    else {HOMEPAGE_FIELD: str(site.homepage_agent_id)}
                )
                | (
                    {}
                    if (preview := _preview_url(scoped, site)) is None
                    else {"preview_url": preview}
                ),
            )
            for name, site in named.items()
        )

    async def member_conversation_rows(
        self,
        ext: ExtensionContext | None,
        conversation_id: UUID,
        *,
        member_id: UUID,
        admin: bool,
        limit: int,
    ) -> tuple[ConversationObjectGrant, ...]:
        agents = await _workspace(ext).agent_visibilities()
        return tuple(
            ConversationObjectGrant(
                name=site_object_name(site.conversation_id, site.name),
                generation=site.generation,
                content_visible=True,
            )
            for site in await _sites(ext).visible_conversation(
                conversation_id,
                member_id,
                limit,
                admin=admin,
                homepage_agents=frozenset(
                    agent_id for agent_id, level in agents.items() if level == "workspace"
                ),
            )
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[SiteSpec] | None:
        site = await self._find(ext, name)
        if site is None:
            return None
        return ObjectDetail(
            spec=SiteSpec(
                visibility=effective_visibility(site, await _workspace(ext).agent_visibilities())
            ),
            created_at=site.created_at,
            updated_at=site.updated_at,
            links=(
                ObjectLink(
                    relation="created_in",
                    target=ObjectRef(kind=CONVERSATION_KIND, name=str(site.conversation_id)),
                ),
            ),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        site = await self._find(ctx.ext, name)
        if site is None:
            return None
        status: dict[str, JsonValue] = {
            "site_name": site.name,
            "port": site.port,
            "creator_member_id": str(site.creator_member_id),
            "site_url": site_url(
                ctx.public_base_url,
                _workspace(ctx.ext).store.workspace_id,
                site.conversation_id,
                site.name,
            ),
            "deploy_generation": site.deploy_generation,
            "homepage_agent": (
                None if site.homepage_agent_id is None else str(site.homepage_agent_id)
            ),
            "source_path": None,
            "files": [],
        }
        if site.source_manifest is not None:
            dest, files = await materialize_source(ctx, site, name)
            status["source_path"] = dest
            status["files"] = list(files)
        return status

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SiteSpec,
        old: SiteSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        """Move the site to the level the spec names, and give a site becoming public the card its
        link will unfurl as.

        A site deployed before cards existed has a picture of its page and no card, and a card is
        only ever published for a public site — so making one public is exactly where that card is
        composed. It is composed from the picture already stored, in the container this turn holds,
        and only the site's creator reaches this path with `public` (an admin may only narrow), so
        the page a card draws is the actor's own. A card that will not draw leaves the site public
        with the generic one, which is what the head already said."""
        if old is None or owner is None:
            raise VerbNotSupported(SITES_ARE_DEPLOYED)
        site = await self._find(ctx.ext, name)
        if site is None:
            raise ValueError(f"site {name!r} was unhosted while its visibility was changing")
        effective = effective_visibility(site, await _workspace(ctx.ext).agent_visibilities())
        if spec.visibility == effective:
            return
        if site.homepage_agent_id is not None:
            raise ValueError(HOMEPAGE_FOLLOWS_AGENT)
        sites = _sites(ctx.ext)
        await sites.set_visibility(site.conversation_id, site.name, spec.visibility)
        if (
            spec.visibility == "public"
            and site.share_card_hash is None
            and site.preview_blob_key is not None
        ):
            await draw_from_stored_shot(
                ctx, sites, site.conversation_id, site.name, site.preview_blob_key
            )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        site = await self._find(ctx.ext, name)
        if site is None:
            raise ValueError(f"site {name!r} was unhosted while it was being unhosted")
        await _sites(ctx.ext).unregister(site.conversation_id, site.name)

    async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None:
        return _named(await _sites(ext).all()).get(name)


SITE_OBJECT = ObjectKind(
    name=SITE_KIND,
    description=(
        "A website a deploy left hosted at a permanent link. Its creator sets who may open it, "
        "and delete unhosts it."
    ),
    guidance=(
        "Sites a deploy left hosted, one object per site and conversation, named "
        "<site-name>-<conversation-digest> (the deploy result carries the name). A site is visible "
        "to the member who deployed it, to workspace admins, and to every member once it is "
        "workspace or public. "
        "Each listed site carries its creator (`owner_email`) and its hosted `site_url`, the link "
        "absent only on a deploy that configures "
        "no public base URL and therefore hosts no reachable link. A site whose last deploy "
        "photographed its page carries `preview_url` too — the signed link to that picture. "
        "Listings filter and order on "
        "`conversation`, `created_at`, `visibility`, and `mine` — filter on this conversation's "
        "id for the sites it hosts, or order by `created_at` desc for the newest. "
        "A site bound as an agent's homepage by set_homepage is the agent's page rather than one "
        "the workspace shares: it is absent from listings unless the read filters on "
        "`homepage_agent` — an agent's id, or `mine` for your own binding — and its visibility "
        "follows the agent's, so an apply naming another level is refused. set_homepage is the "
        "agent object's own action: object_get with an empty ref returns your own agent and its "
        "actions, and another agent's page is bound only by its owner or an admin. "
        "object_get returns its visibility, and its status carries the hosted site_url, its "
        "creator, and — for a deployed static site — its stored source, materialized into the "
        "sandbox at the status's source_path: edit there and deploy_website that directory to "
        "update the page. "
        "The `created_in` link names the conversation that built it. Apply a "
        "manifest whose spec changes only `visibility` — private (creator "
        "and admins), workspace (any signed-in member), or public (anyone with the link) — the "
        "same act "
        "the member can perform in the site's own frame. Create and any other spec change are "
        "refused: a site exists by serving a port, so build it and deploy_website it. Delete "
        "unregisters the site and its link stops resolving; the sandbox keeps the port until its "
        "own lifecycle ends, so re-deploying hosts it again."
    ),
    spec_model=SiteSpec,
    store=SiteObjects(),
    list_fields=frozenset(
        {
            "conversation",
            "created_at",
            "visibility",
            "mine",
            "site_url",
            "preview_url",
            "owner_email",
            HOMEPAGE_FIELD,
            "deploy_generation",
        }
    ),
)
