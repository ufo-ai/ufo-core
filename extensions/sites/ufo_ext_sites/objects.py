"""The `site` object kind: hosted sites as workspace objects, so chat reaches the same visibility
column the in-frame selector writes.

One object per registered site, named `<site-name>-<conversation-digest>` (`dashboard-9f21c0a4e3b7`,
carried in the deploy result), because two conversations may each host a `dashboard` and each keeps
its own link. A site belongs to the member who deployed it: its creator sees it whatever its
visibility, every other member sees it once it is no longer private, and only the creator may change
who can open it — a workspace admin may narrow a site to private but never widen one. Create is
refused naming `deploy_website`: a site exists by serving a port, and only a deploy knows which
port. Delete unregisters it, and the link stops resolving."""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
    CONVERSATION_KIND,
    MemberOwnedObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectOwner,
    ObjectRef,
    OwnedRow,
    VerbNotSupported,
)
from ufo.sdk.tools import ToolContext
from ufo_ext_sites.store import SITE_VISIBILITY_GATE, HostedSite, HostedSites, Visibility
from ufo_ext_sites.surface import site_url

SITE_KIND = "site"
SITES_ARE_DEPLOYED = (
    "sites exist only by deploying one — build the site in the workspace and deploy_website it"
)
VISIBILITY_GATE = f"{SITE_VISIBILITY_GATE}; a workspace admin may only make it private"
UNHOST_GATE = "only the member who deployed a site, or a workspace admin, may unhost it"
CONVERSATION_DIGEST_HEX = 12


class SiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Visibility = Field(
        description=(
            "Who may open the site's link: private (its creator alone), workspace (any member of "
            "the workspace, signed in), or public (anyone holding the link)."
        )
    )


def site_object_name(conversation_id: UUID, name: str) -> str:
    """The object name for one site's identity: its own name plus a digest of the conversation that
    hosts it, so the same site name in two conversations addresses two objects."""
    digest = hashlib.sha256(str(conversation_id).encode()).hexdigest()[:CONVERSATION_DIGEST_HEX]
    return f"{name}-{digest}"


def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]:
    return {site_object_name(site.conversation_id, site.name): site for site in sites}


def _workspace(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("the site kind dispatched without its ExtensionContext")
    return ctx.ext


def _sites(ctx: ToolContext) -> HostedSites:
    ext = _workspace(ctx)
    return HostedSites(ext.store.workspace_id, ext.transaction)


def _summary(site: HostedSite) -> str:
    return f"{site.name} · {site.visibility} · sandbox port {site.port}"


@dataclass(frozen=True)
class SiteObjects(MemberOwnedObjects[SiteSpec, ObjectOwner]):
    """Read, re-gate, and unhost handlers over the workspace's registered sites. Ownership is the
    site's creator and disclosure is its own `visibility` column — the gate the frame enforces per
    visit, read here through the one registry both surfaces write."""

    kind_name: ClassVar[str] = SITE_KIND
    mutate_gate: ClassVar[str] = VISIBILITY_GATE
    delete_gate: ClassVar[str] = UNHOST_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool:
        return old.visibility != "private" and spec.visibility == "private"

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]:
        return await self._member_rows(_workspace(ctx))

    async def _member_rows(self, ext: ExtensionContext | None) -> tuple[OwnedRow[ObjectOwner], ...]:
        if ext is None:
            raise RuntimeError("the site kind reads through its ExtensionContext")
        sites = HostedSites(ext.store.workspace_id, ext.transaction)
        return tuple(
            OwnedRow(
                name=name,
                summary=_summary(site),
                owner=ObjectOwner(
                    member_id=site.creator_member_id, shared=site.visibility != "private"
                ),
                fields={
                    "conversation": str(site.conversation_id),
                    "created_at": site.created_at.isoformat(),
                    "visibility": site.visibility,
                },
            )
            for name, site in _named(await sites.all()).items()
        )

    async def _detail(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> ObjectDetail[SiteSpec] | None:
        site = await self._find(ctx, name)
        if site is None:
            return None
        return ObjectDetail(
            spec=SiteSpec(visibility=site.visibility),
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
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        site = await self._find(ctx, name)
        if site is None:
            return None
        return {
            "site_name": site.name,
            "port": site.port,
            "creator_member_id": str(site.creator_member_id),
            "site_url": site_url(
                ctx.public_base_url,
                _workspace(ctx).store.workspace_id,
                site.conversation_id,
                site.name,
            ),
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SiteSpec,
        old: SiteSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        if old is None or owner is None:
            raise VerbNotSupported(SITES_ARE_DEPLOYED)
        site = await self._find(ctx, name)
        if site is None:
            raise ValueError(f"site {name!r} was unhosted while its visibility was changing")
        if spec.visibility == site.visibility:
            return
        await _sites(ctx).set_visibility(site.conversation_id, site.name, spec.visibility)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        site = await self._find(ctx, name)
        if site is None:
            raise ValueError(f"site {name!r} was unhosted while it was being unhosted")
        await _sites(ctx).unregister(site.conversation_id, site.name)

    async def _find(self, ctx: ToolContext, name: str) -> HostedSite | None:
        return _named(await _sites(ctx).all()).get(name)


SITE_OBJECT = ObjectKind(
    name=SITE_KIND,
    description=(
        "A website hosted at a permanent link by deploy_website or publish_website: list this "
        "workspace's sites, get one for its link and visibility, apply to change who may open it, "
        "delete to unhost it. Create is refused — a deploy hosts a site. Only the site's creator "
        "may change its visibility; a workspace admin may only make it private."
    ),
    guidance=(
        "Sites a deploy left hosted, one object per site and conversation, named "
        "<site-name>-<conversation-digest> (the deploy result carries the name). A site is visible "
        "to the member who deployed it, and to every member once it is workspace or public. "
        "Listings filter and order on `conversation`, `created_at`, and `visibility` — filter on "
        "this conversation's id for the sites it hosts, or order by `created_at` desc for the "
        "newest. "
        "object_get returns its visibility, and its status carries the hosted site_url, the "
        "sandbox port serving it, and its creator; the `created_in` link names the conversation "
        "that built it. Apply a manifest whose spec changes only `visibility` — private (creator "
        "alone), workspace (any signed-in member), or public (anyone with the link) — the same act "
        "the member can perform in the site's own frame. Create and any other spec change are "
        "refused: a site exists by serving a port, so build it and deploy_website it. Delete "
        "unregisters the site and its link stops resolving; the sandbox keeps the port until its "
        "own lifecycle ends, so re-deploying hosts it again."
    ),
    spec_model=SiteSpec,
    store=SiteObjects(),
    list_fields=frozenset({"conversation", "created_at", "visibility"}),
)
