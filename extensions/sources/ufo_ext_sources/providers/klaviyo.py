"""The Klaviyo connector — profiles, lists, segments, campaigns, flows, events, and the catalog/
admin adjuncts synced into recallable pages over Klaviyo's JSON:API surface.

One pagination shape covers everything: each list response carries `links.next` (an absolute URL
whose path+query the connector follows until it is missing). Incremental streams filter with
Klaviyo's expression DSL — `greater-or-equal(<field>,<cursor>)` sorted ascending — where the field
is `updated` for most resources, `updated_at` for campaigns/forms/images, and `datetime` for the
append-only events feed. Auth is Klaviyo's private-key scheme (`Authorization: Klaviyo-API-Key
<key>`, not a bearer) plus a pinned `revision` header, layered on whichever client the base built
from the resolved `Credential`.

Klaviyo wraps every record as `{type, id, attributes, relationships, links}`, so the incremental
cursor lives under `attributes`; `flatten` lifts `attributes.*` to the top level (that is what makes
the watermark advance) and additionally surfaces the nested relationship/sub-attribute fields a
record's recall benefits from (a profile's consent, a campaign's subject, an event's metric name and
references). List and segment profile counts are neither requested nor rendered: membership changes
must not change those records. A refusal (HTTP 401/403) raises `StreamSkipped` so the run records a
skip. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

KLAVIYO_REVISION = "2024-10-15"
PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})
_DATETIME_CURSOR_STREAMS = {"events"}
_UPDATED_AT_CURSOR_STREAMS = {"campaigns", "forms", "images"}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated",
    created_at_field: str = "created",
    updated_at_field: str | None = "updated",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
    )


KLAVIYO_STREAMS: list[StreamSpec] = [
    _stream("profiles", canonical=True),
    _stream("lists", canonical=True),
    _stream("segments", canonical=True),
    _stream(
        "campaigns",
        cursor_field="updated_at",
        created_at_field="created_at",
        updated_at_field="updated_at",
        canonical=True,
    ),
    _stream("flows", canonical=True),
    _stream(
        "events",
        cursor_field="datetime",
        created_at_field="datetime",
        updated_at_field=None,
        canonical=True,
    ),
    _stream("metrics"),
    _stream("templates", source_object="templates"),
    _stream("tags", cursor_field=None),
    _stream("tag_groups", source_object="tag-groups", cursor_field=None),
    _stream(
        "forms",
        cursor_field="updated_at",
        created_at_field="created_at",
        updated_at_field="updated_at",
    ),
    _stream("coupons", cursor_field=None),
    _stream("coupon_codes", source_object="coupon-codes", cursor_field=None),
    _stream("catalog_items", source_object="catalog-items"),
    _stream("catalog_variants", source_object="catalog-variants"),
    _stream("catalog_categories", source_object="catalog-categories"),
    _stream(
        "images",
        cursor_field="updated_at",
        created_at_field="created_at",
        updated_at_field="updated_at",
    ),
    _stream("push_tokens", source_object="push-tokens", cursor_field=None),
    _stream("webhooks", cursor_field=None),
    _stream("reviews", cursor_field="updated"),
    _stream(
        "data_privacy_deletion_jobs", source_object="data-privacy-deletion-jobs", cursor_field=None
    ),
    _stream("accounts", cursor_field=None),
    _stream("campaign_messages", source_object="campaign-messages", cursor_field=None),
]


class KlaviyoConnector(RestConnector):
    name = "klaviyo"
    base_url = "https://a.klaviyo.com"
    streams_list = KLAVIYO_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        """Layer Klaviyo's pinned `revision` header on the base client, and — when the credential
        is a member-added key read host-side — its private-key auth scheme (a broker's transport
        injects auth itself, so the direct override applies only when a bearer is present)."""
        client = super()._make_client(base_url, credential)
        client.headers["revision"] = KLAVIYO_REVISION
        if credential.bearer is not None:
            client.headers["Authorization"] = f"Klaviyo-API-Key {credential.bearer}"
        return client

    @staticmethod
    def _next_path(next_link: str | None) -> str | None:
        """Strip a Klaviyo `links.next` absolute URL down to the path+query the base-bound client
        needs. None when the link is empty."""
        if not next_link:
            return None
        parsed = urlparse(next_link)
        if not parsed.path:
            return None
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path

    @staticmethod
    def _cursor_field_for(stream: StreamSpec) -> str:
        if stream.name in _DATETIME_CURSOR_STREAMS:
            return "datetime"
        if stream.name in _UPDATED_AT_CURSOR_STREAMS:
            return "updated_at"
        return "updated"

    @staticmethod
    def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        """The first-page query. Later pages ride the absolute `links.next` URL and skip this."""
        params: dict[str, Any] = {"page[size]": PAGE_SIZE}
        if stream.cursor_field and cursor:
            field = KlaviyoConnector._cursor_field_for(stream)
            params["filter"] = f"greater-or-equal({field},{cursor})"
            params["sort"] = field
        elif stream.cursor_field:
            field = KlaviyoConnector._cursor_field_for(stream)
            params["sort"] = field
        if stream.name == "profiles":
            params["additional-fields[profile]"] = "subscriptions"
        elif stream.name == "events":
            params["include"] = "metric"
        return params

    @staticmethod
    def _lift_relationship_id(rels: Any, key: str) -> str | None:
        """Read `relationships[<key>].data.id` defensively; Klaviyo omits empty relationships."""
        if not isinstance(rels, dict):
            return None
        rel = rels.get(key)
        if not isinstance(rel, dict):
            return None
        data = rel.get("data")
        if not isinstance(data, dict):
            return None
        rid = data.get("id")
        return str(rid) if rid is not None else None

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift `attributes.*` to the top level (so the incremental cursor and the record's fields
        become flat keys) plus the nested relationship/sub-attribute values a record recalls by."""
        attrs = record.get("attributes")
        flat: dict[str, Any] = {"id": record.get("id")}
        if record.get("type") is not None:
            flat["resource_type"] = record["type"]
        if isinstance(attrs, dict):
            flat.update(attrs)
        if stream.name in {"lists", "segments"}:
            flat.pop("profile_count", None)

        rels = record.get("relationships")

        if stream.name == "profiles":
            self._flatten_profile(flat, attrs)
        elif stream.name == "campaigns":
            self._flatten_campaign(flat, attrs)
        elif stream.name == "events":
            flat["profile_id"] = self._lift_relationship_id(rels, "profile")
            flat["metric_id"] = self._lift_relationship_id(rels, "metric")
            props = attrs.get("event_properties") if isinstance(attrs, dict) else None
            if isinstance(props, dict):
                flat["message_id"] = props.get("$message") or props.get("$flow")

        elif stream.name == "segments":
            list_id = self._lift_relationship_id(rels, "list")
            if list_id is not None:
                flat["parent_list_id"] = list_id

        return flat

    @staticmethod
    def _flatten_profile(flat: dict[str, Any], attrs: Any) -> None:
        subs = attrs.get("subscriptions") if isinstance(attrs, dict) else None
        email_block = subs.get("email") if isinstance(subs, dict) else None
        marketing = email_block.get("marketing") if isinstance(email_block, dict) else None
        if not isinstance(marketing, dict):
            return
        flat["email_consent"] = marketing.get("consent")
        suppression = marketing.get("suppression") or marketing.get("suppressions")
        if isinstance(suppression, list) and suppression:
            head = suppression[0]
            flat["email_suppression"] = (
                head.get("reason") or head.get("code") if isinstance(head, dict) else str(head)
            )
        elif isinstance(suppression, str):
            flat["email_suppression"] = suppression

    @staticmethod
    def _flatten_campaign(flat: dict[str, Any], attrs: Any) -> None:
        audiences = attrs.get("audiences") if isinstance(attrs, dict) else None
        if isinstance(audiences, dict):
            message = audiences.get("message_settings")
            if isinstance(message, dict):
                flat["subject_line"] = message.get("subject_line") or message.get("subject")
                flat["from_label"] = message.get("from_label") or message.get("from_name")
                flat["from_email"] = message.get("from_email")
            included = audiences.get("included")
            if isinstance(included, list) and included:
                first = included[0]
                flat["primary_list_id"] = (
                    str(first) if not isinstance(first, dict) else first.get("id")
                )
        flat.setdefault(
            "subject_line", attrs.get("subject_line") if isinstance(attrs, dict) else None
        )
        flat.setdefault("from_label", attrs.get("from_label") if isinstance(attrs, dict) else None)
        flat.setdefault("from_email", attrs.get("from_email") if isinstance(attrs, dict) else None)

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path: str | None = f"/api/{stream.source_object}"
        params: dict[str, Any] | None = self._initial_query(stream, cursor)
        wants_metric_lookup = stream.name == "events"
        try:
            while path:
                data = await self._get(client, path, params=params)
                params = None
                records = data.get("data") or []
                included = data.get("included") if wants_metric_lookup else None
                if wants_metric_lookup and isinstance(included, list):
                    metric_names: dict[str, str] = {}
                    for item in included:
                        if not isinstance(item, dict) or item.get("type") != "metric":
                            continue
                        mid = item.get("id")
                        name = (
                            (item.get("attributes") or {}).get("name")
                            if isinstance(item.get("attributes"), dict)
                            else None
                        )
                        if isinstance(mid, str) and isinstance(name, str):
                            metric_names[mid] = name
                    for record in records:
                        if not isinstance(record, dict):
                            continue
                        rels = record.get("relationships")
                        metric_rel = rels.get("metric") if isinstance(rels, dict) else None
                        md = metric_rel.get("data") if isinstance(metric_rel, dict) else None
                        name = metric_names.get(str(md.get("id"))) if isinstance(md, dict) else None
                        if name is not None:
                            attrs = record.get("attributes")
                            if isinstance(attrs, dict):
                                attrs["metric_name"] = name
                            else:
                                record["attributes"] = {"metric_name": name}
                if records:
                    yield records
                links = data.get("links") or {}
                next_link = links.get("next") if isinstance(links, dict) else None
                path = self._next_path(next_link if isinstance(next_link, str) else None)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"klaviyo: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise
