"""The ActiveCampaign v3 connector — the marketing + CRM surface (contacts, lists, campaigns,
deals, accounts, custom fields, and their junction/substream collections) synced as recallable
pages.

ActiveCampaign speaks one shape on every list endpoint: a resource-keyed envelope
`{<resource>: [...], meta: {total: N}}` paged by `?limit=100&offset=N`. `paginate` resolves each
stream's (URL segment, envelope key) — usually identical to the stream name, camelCase for compound
resources (`accountContacts`, `dealActivities`) — and walks offsets until a short page. Most
resources accept a `filters[<field>_after]=<iso>` incremental filter, threaded from the run cursor
when present; the rest full-refresh and the row-level cursor keeps re-reads deduplicated. Auth is
the account `Api-Token` header (not a bearer): when the resolved `Credential` carries a direct key,
`_make_client` sends it as `Api-Token`; a broker's proxying transport is honored unchanged. The base
URL is per-tenant (`https://<account>.api-us1.com`), so the class default is empty and a run without
a resolved host fails loud. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_LIMIT = 100
API_PREFIX = "/api/3"
_REFUSAL_STATUS = frozenset({401, 403})

_STREAM_PATHS: dict[str, tuple[str, str]] = {
    "contacts": ("contacts", "contacts"),
    "lists": ("lists", "lists"),
    "segments": ("segments", "segments"),
    "campaigns": ("campaigns", "campaigns"),
    "automations": ("automations", "automations"),
    "campaign_messages": ("campaignMessages", "campaignMessages"),
    "deals": ("deals", "deals"),
    "deal_groups": ("dealGroups", "dealGroups"),
    "deal_stages": ("dealStages", "dealStages"),
    "deal_activities": ("dealActivities", "dealActivities"),
    "deal_tasks": ("dealTasks", "dealTasks"),
    "deal_task_types": ("dealTasktypes", "dealTasktypes"),
    "accounts": ("accounts", "accounts"),
    "account_contacts": ("accountContacts", "accountContacts"),
    "account_custom_field_meta": ("accountCustomFieldMeta", "accountCustomFieldMeta"),
    "account_custom_field_data": ("accountCustomFieldData", "accountCustomFieldData"),
    "forms": ("forms", "forms"),
    "tags": ("tags", "tags"),
    "contact_tags": ("contactTags", "contactTags"),
    "contact_lists": ("contactLists", "contactLists"),
    "contact_automations": ("contactAutomations", "contactAutomations"),
    "contact_deals": ("contactDeals", "contactDeals"),
    "fields": ("fields", "fields"),
    "field_values": ("fieldValues", "fieldValues"),
    "field_options": ("fieldOptions", "fieldOptions"),
    "field_relationships": ("fieldRels", "fieldRels"),
    "notes": ("notes", "notes"),
    "saved_responses": ("savedResponses", "savedResponses"),
    "templates": ("templates", "templates"),
    "messages": ("messages", "messages"),
    "addresses": ("addresses", "addresses"),
    "address_lists": ("addressLists", "addressLists"),
    "address_groups": ("addressGroups", "addressGroups"),
    "bounce_logs": ("bounceLogs", "bounceLogs"),
    "site_tracking_domains": ("siteTrackingDomains", "siteTrackingDomains"),
    "event_tracking_events": ("eventTrackingEvents", "eventTrackingEvents"),
    "goals": ("goals", "goals"),
    "sms": ("sms", "sms"),
    "calendar_feeds": ("calendarFeeds", "calendarFeeds"),
    "webhooks": ("webhooks", "webhooks"),
    "users": ("users", "users"),
    "groups": ("groups", "groups"),
    "brandings": ("brandings", "brandings"),
    "scores": ("scores", "scores"),
    "score_values": ("scoreValues", "scoreValues"),
    "segment_conditions": ("segmentConditions", "segmentConditions"),
}

_INCREMENTAL_FILTER_STREAMS: dict[str, str] = {
    "contacts": "udate",
    "campaigns": "mdate",
    "automations": "mdate",
    "deals": "mdate",
    "accounts": "udate",
    "lists": "udate",
    "messages": "mdate",
    "notes": "mdate",
    "goals": "mdate",
    "sms": "mdate",
    "scores": "mdate",
}
_UPDATED_AT_FIELDS = frozenset({"mdate", "udate", "updated_timestamp"})


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "udate",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field="cdate",
        updated_at_field=cursor_field if cursor_field in _UPDATED_AT_FIELDS else None,
        canonical=canonical,
    )


ACTIVECAMPAIGN_STREAMS: list[StreamSpec] = [
    _stream("contacts", canonical=True),
    _stream("lists"),
    _stream("segments", cursor_field=None),
    _stream("campaigns", cursor_field="mdate", canonical=True),
    _stream("automations", cursor_field="mdate"),
    _stream(
        "campaign_messages", source_object="campaignMessages", cursor_field=None, canonical=True
    ),
    _stream("deals", cursor_field="mdate", canonical=True),
    _stream("deal_groups", source_object="dealGroups"),
    _stream("deal_stages", source_object="dealStages"),
    _stream("deal_activities", source_object="dealActivities", cursor_field="cdate"),
    _stream("deal_tasks", source_object="dealTasks", cursor_field="udate", canonical=True),
    _stream("deal_task_types", source_object="dealTaskTypes", cursor_field=None),
    _stream("accounts", cursor_field="udate", canonical=True),
    _stream("account_contacts", source_object="accountContacts", cursor_field="udate"),
    _stream("account_custom_field_meta", source_object="accountCustomFieldMeta", cursor_field=None),
    _stream(
        "account_custom_field_data", source_object="accountCustomFieldData", cursor_field="udate"
    ),
    _stream("forms"),
    _stream("tags"),
    _stream("contact_tags", source_object="contactTags", cursor_field=None),
    _stream("contact_lists", source_object="contactLists", cursor_field="updated_timestamp"),
    _stream("contact_automations", source_object="contactAutomations", cursor_field="adddate"),
    _stream("contact_deals", source_object="contactDeals", cursor_field=None),
    _stream("fields"),
    _stream("field_values", source_object="fieldValues", cursor_field="udate"),
    _stream("field_options", source_object="fieldOptions", cursor_field=None),
    _stream("field_relationships", source_object="fieldRels", cursor_field=None),
    _stream("notes", cursor_field="mdate", canonical=True),
    _stream("saved_responses", source_object="savedResponses", cursor_field=None),
    _stream("templates"),
    _stream("messages", cursor_field="mdate"),
    _stream("addresses", cursor_field=None),
    _stream("address_lists", source_object="addressLists", cursor_field=None),
    _stream("address_groups", source_object="addressGroups", cursor_field=None),
    _stream("bounce_logs", source_object="bounceLogs", cursor_field="cdate"),
    _stream("site_tracking_domains", source_object="siteTrackingDomains", cursor_field=None),
    _stream("event_tracking_events", source_object="eventTrackingEvents", cursor_field=None),
    _stream("goals", cursor_field="mdate"),
    _stream("sms", cursor_field="mdate"),
    _stream("calendar_feeds", source_object="calendarFeeds", cursor_field=None),
    _stream("webhooks", cursor_field=None),
    _stream("users", cursor_field=None),
    _stream("groups", cursor_field=None),
    _stream("brandings", cursor_field=None),
    _stream("scores", cursor_field="mdate"),
    _stream("score_values", source_object="scoreValues", cursor_field="udate"),
    _stream("segment_conditions", source_object="segmentConditions", cursor_field=None),
]


class ActiveCampaignConnector(RestConnector):
    name = "active_campaign"
    base_url = ""
    streams_list = ACTIVECAMPAIGN_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        if credential.transport is not None:
            return super()._make_client(base_url, credential)
        if credential.bearer is None:
            raise RuntimeError("active_campaign: credential carries no api key")
        return super()._make_client(base_url, Credential(headers={"Api-Token": credential.bearer}))

    @staticmethod
    def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]:
        return _STREAM_PATHS.get(stream.name, (stream.name, stream.name))

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        url_segment, envelope_key = self._resolve_stream_segment(stream)
        path = f"{API_PREFIX}/{url_segment}"
        base_params: dict[str, Any] = {"limit": PAGE_LIMIT}
        filter_key = _INCREMENTAL_FILTER_STREAMS.get(stream.name)
        if cursor and filter_key:
            base_params[f"filters[{filter_key}_after]"] = cursor
        try:
            async for page in self._get_offset_pages(
                client,
                path,
                records_path=envelope_key,
                limit=PAGE_LIMIT,
                params=base_params,
            ):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"active_campaign: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks scope or the key is invalid"
                ) from error
            raise
