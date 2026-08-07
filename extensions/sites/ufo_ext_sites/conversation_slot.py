from ufo.sdk.manifest import (
    CONVERSATION_SITES_MAX,
    ConversationSite,
    ConversationSlotContext,
    ConversationSlotProvider,
    SitesSlotPayload,
)
from ufo_ext_sites.objects import site_name_from_object, site_object_name
from ufo_ext_sites.store import HostedSites
from ufo_ext_sites.surface import site_url


async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload:
    expected = {
        item.name: (name, item.generation)
        for item in ctx.visible_items
        if (name := site_name_from_object(ctx.conversation_id, item.name)) is not None
    }
    rows = await HostedSites(ctx.ext.store.workspace_id, ctx.ext.transaction).conversation(
        ctx.conversation_id,
        tuple(name for name, _generation in expected.values()),
        CONVERSATION_SITES_MAX + 1,
    )
    authorized = tuple(
        row
        for row in rows
        if expected.get(site_object_name(ctx.conversation_id, row.name))
        == (row.name, row.generation)
    )
    sites = tuple(
        ConversationSite(
            name=row.name,
            url=site_url(
                ctx.public_base_url,
                ctx.ext.store.workspace_id,
                ctx.conversation_id,
                row.name,
            ),
            visibility=row.visibility,
            created_at=row.created_at,
            updated_at=row.updated_at,
            authorization_name=site_object_name(ctx.conversation_id, row.name),
            authorization_generation=row.generation,
        )
        for row in authorized[:CONVERSATION_SITES_MAX]
    )
    return SitesSlotPayload(sites=sites, truncated=len(authorized) > CONVERSATION_SITES_MAX)


async def _summarize(ctx: ConversationSlotContext) -> int | None:
    count = min(len(ctx.visible_items), CONVERSATION_SITES_MAX)
    return count or None


SITES_SLOT = ConversationSlotProvider(
    id="sites",
    label="Sites",
    icon="link",
    content=SitesSlotPayload,
    summarize=_summarize,
    read=_read,
)
