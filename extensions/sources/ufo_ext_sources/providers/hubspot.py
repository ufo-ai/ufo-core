"""The HubSpot connector — CRM objects, activities, commerce, marketing, and the product-API
surfaces synced into recallable pages.

CRM object records come from the search API (`POST /crm/v3/objects/<type>/search`), paginating with
the `paging.next.after` opaque cursor; an incremental run filters on `hs_lastmodifieddate >= cursor`
(`lastmodifieddate` on contacts, the one object HubSpot names it that way)
ordered ascending (HubSpot's `GTE` is inclusive, so the connector dedupes the equal-boundary
records by id). Each object stream follows its search with a cheap archived-id sweep against the
list endpoint so a deleted record lands as a tombstone. Product APIs that CRM search doesn't expose
(owners, lists, workflows, conversations, CMS assets, analytics, events, associations, sequences,
…) use their own list endpoints — the flat ones through a declared `next_cursor` `Pagination`, the
rest through per-stream fan-out walks.

`flatten` lifts HubSpot's `{ id, properties: {...}, createdAt, updatedAt }` envelope so the
`hs_lastmodifieddate` cursor and every property read as top-level keys; product-API rows are
normalized by lifting their `objectId`, `properties`, and `values[]` shapes into the same flat
record. An object HubSpot gates for this account by product tier or OAuth scope surfaces as a `403`
whose body names the missing permission, and the connector raises `StreamSkipped` so the run records
a skip rather than a failure. The credential is resolved through the auth proxy the runner threads —
this connector holds no token. The write path is intentionally absent — the source seam only reads.
"""

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from typing import Any
from urllib.parse import quote

import httpx

from ufo.sdk.sources import (
    Pagination,
    PaginationStrategy,
    RestConnector,
    StreamPage,
    StreamSkipped,
    StreamSpec,
)

PAGE_LIMIT = 100
_FORMS_SUBMISSIONS_LIMIT = 50
_ANALYTICS_REPORT_LIMIT = 350
_ANALYTICS_REPORT_START = "20000101"
_EMAIL_EVENT_LIMIT = 1000
_PIPELINE_OBJECT_TYPES = ("deals", "tickets")
_CUSTOM_OBJECTS_STREAM = "custom_objects"
_CUSTOM_OBJECT_SCHEMA_PATH = "/crm/v3/schemas"
_ASSOCIATION_LABELS_STREAM = "association_labels"
_ASSOCIATIONS_STREAM = "associations"
_LIST_MEMBERSHIPS_STREAM = "list_memberships"
_SUBSCRIPTION_DEFINITIONS_STREAM = "subscription_definitions"
_CONSENT_STATES_STREAM = "consent_states"
_SEQUENCES_STREAM = "sequences"
_SEQUENCE_ENROLLMENTS_STREAM = "sequence_enrollments"
_PRODUCT_API_STREAMS = frozenset(
    {
        "owners",
        "owner_teams",
        "pipelines",
        "pipeline_stages",
        "lists",
        "workflows",
        "campaigns",
        "campaign_assets",
        "marketing_emails",
        "marketing_events",
        "forms",
        "form_submissions",
        "conversations",
        "conversation_messages",
        "knowledge_articles",
        "site_pages",
        "landing_pages",
        "blog_posts",
        "files",
        "analytics_views",
        "analytics_reports",
        "event_types",
        "event_occurrences",
        "email_events",
        _ASSOCIATION_LABELS_STREAM,
        _ASSOCIATIONS_STREAM,
        _LIST_MEMBERSHIPS_STREAM,
        _SUBSCRIPTION_DEFINITIONS_STREAM,
        _CONSENT_STATES_STREAM,
        _SEQUENCES_STREAM,
        _SEQUENCE_ENROLLMENTS_STREAM,
    }
)
_PASSTHROUGH_STREAMS = _PRODUCT_API_STREAMS

_GET_PRODUCT_API_PATHS: dict[str, str] = {
    "owners": "/crm/v3/owners",
    "workflows": "/automation/v4/flows",
    "campaigns": "/marketing/campaigns/2026-03",
    "marketing_emails": "/marketing/emails/2026-03",
    "marketing_events": "/marketing/marketing-events/2026-03",
    "forms": "/marketing/v3/forms",
    "conversations": "/conversations/v3/conversations/threads",
    "knowledge_articles": "/cms/v3/site-search/search",
    "site_pages": "/cms/pages/2026-03/site-pages",
    "landing_pages": "/cms/pages/2026-03/landing-pages",
    "blog_posts": "/cms/blogs/2026-03/posts",
    "files": "/files/2026-03/files/search",
    "event_types": "/events/v3/events/event-types",
}

_ASSOCIATION_STANDARD_OBJECT_TYPES = (
    "companies",
    "contacts",
    "deals",
    "tickets",
    "leads",
    "appointments",
    "calls",
    "emails",
    "meetings",
    "notes",
    "tasks",
    "communications",
    "postal_mail",
    "invoices",
    "commerce_payments",
    "quotes",
    "line_items",
    "orders",
    "carts",
    "discounts",
    "fees",
    "subscriptions",
    "taxes",
    "courses",
    "listings",
    "services",
    "feedback_submissions",
    "projects",
    "goal_targets",
    "products",
)

_CAMPAIGN_ASSET_TYPES = (
    "AD_CAMPAIGN",
    "BLOG_POST",
    "CALL",
    "CASE_STUDY",
    "WEB_INTERACTIVE",
    "CTA",
    "EXTERNAL_WEB_URL",
    "FEEDBACK_SURVEY",
    "FORM",
    "FILE_MANAGER_FILE",
    "KNOWLEDGE_ARTICLE",
    "LANDING_PAGE",
    "MARKETING_EMAIL",
    "MARKETING_EVENT",
    "MEETING_EVENT",
    "PLAYBOOK",
    "PODCAST_EPISODE",
    "SALES_DOCUMENT",
    "EMAIL",
    "SEQUENCE",
    "MARKETING_SMS",
    "SOCIAL_BROADCAST",
    "OBJECT_LIST",
    "MEDIA",
    "SITE_PAGE",
    "AUTOMATION_PLATFORM_FLOW",
)

_MARKETING_ASSET_KIND_BY_TYPE = {
    "AD_CAMPAIGN": "ad_campaign",
    "BLOG_POST": "blog_post",
    "CALL": "call",
    "CASE_STUDY": "case_study",
    "WEB_INTERACTIVE": "cta",
    "CTA": "cta_legacy",
    "EXTERNAL_WEB_URL": "external_web_url",
    "FEEDBACK_SURVEY": "feedback_survey",
    "FORM": "form",
    "FILE_MANAGER_FILE": "file",
    "KNOWLEDGE_ARTICLE": "knowledge_article",
    "LANDING_PAGE": "landing_page",
    "MARKETING_EMAIL": "marketing_email",
    "MARKETING_EVENT": "marketing_event",
    "MEETING_EVENT": "meeting",
    "PLAYBOOK": "playbook",
    "PODCAST_EPISODE": "podcast_episode",
    "SALES_DOCUMENT": "sales_document",
    "EMAIL": "sales_email",
    "SEQUENCE": "sequence",
    "MARKETING_SMS": "sms",
    "SOCIAL_BROADCAST": "social_post",
    "OBJECT_LIST": "static_list",
    "MEDIA": "video",
    "SITE_PAGE": "site_page",
    "AUTOMATION_PLATFORM_FLOW": "workflow",
}

_ANALYTICS_REPORT_BREAKDOWN_TYPES = (
    "totals",
    "sessions",
    "sources",
    "geolocation",
    "utm-campaigns",
    "utm-contents",
    "utm-mediums",
    "utm-sources",
    "utm-terms",
)

_ANALYTICS_REPORT_OBJECT_TYPES = (
    "event-completions",
    "forms",
    "pages",
    "social-assists",
)

_ANALYTICS_REPORT_TIME_PERIODS = (
    "total",
    "daily",
    "weekly",
    "monthly",
    "summarize/daily",
    "summarize/weekly",
    "summarize/monthly",
)

# Junction streams: stream name → (parent_object, target_object). The parent slug is the v3 list
# endpoint to walk; the target slug is the associations relation embedded via `?associations=`.
_JUNCTION_STREAMS: dict[str, tuple[str, str]] = {
    "deal_contacts": ("deals", "contacts"),
    "deal_companies": ("deals", "companies"),
    "ticket_contacts": ("tickets", "contacts"),
    "ticket_companies": ("tickets", "companies"),
    "task_contacts": ("tasks", "contacts"),
    "task_companies": ("tasks", "companies"),
}

_OBJECT_SINGULARS: dict[str, str] = {
    "deals": "deal",
    "tickets": "ticket",
    "tasks": "task",
    "contacts": "contact",
    "companies": "company",
}


def _normalize_epoch_millis(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    if isinstance(value, str) and value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000, UTC).isoformat()
    return value


def _stream(
    name: str,
    *,
    object_type: str,
    canonical: bool = True,
    modified_property: str = "hs_lastmodifieddate",
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=object_type,
        primary_key="id",
        cursor_field=modified_property,
        created_at_field="createdAt",
        updated_at_field=modified_property,
        canonical=canonical,
    )


def _product_api_stream(
    name: str,
    *,
    source_object: str,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str | None = "createdAt",
    updated_at_field: str | None = "updatedAt",
    pagination: Pagination | None = None,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=False,
        pagination=pagination,
    )


def _hubspot_get_pagination(path: str) -> Pagination:
    """HubSpot's standard GET-collection shape: a `results[]` body, `paging.next.after` cursor, the
    `after` query param, default `limit=100`. Covers every flat product-API list endpoint — they
    differ only in URL."""
    return Pagination(
        strategy=PaginationStrategy.next_cursor,
        path=path,
        record_path="results",
        cursor_path="paging.next.after",
        cursor_param="after",
        page_size_param="limit",
        page_size=100,
    )


def _junction(name: str, *, parent_object: str) -> StreamSpec:
    """A synthetic junction stream populated from HubSpot's `?associations=` parameter on the parent
    object's list endpoint. Each row is one (parent, target) pair; no cursor — HubSpot exposes no
    modification timestamp on associations, so it full-refreshes each run, idempotent via upsert."""
    return StreamSpec(
        name=name,
        source_object=parent_object,
        primary_key="id",
        cursor_field=None,
        canonical=False,
    )


COMPANIES = _stream("companies", object_type="companies")
CONTACTS = _stream("contacts", object_type="contacts", modified_property="lastmodifieddate")
DEALS = _stream("deals", object_type="deals")
TASKS = _stream("tasks", object_type="tasks")

LEADS = _stream("leads", object_type="leads", canonical=False)
TICKETS = _stream("tickets", object_type="tickets", canonical=False)
USERS = _stream("users", object_type="users", canonical=False)
OWNERS = _product_api_stream(
    "owners",
    source_object="owners",
    primary_key="id",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/crm/v3/owners"),
)
OWNER_TEAMS = _product_api_stream(
    "owner_teams",
    source_object="owner_teams",
    created_at_field=None,
    updated_at_field=None,
)

APPOINTMENTS = _stream("appointments", object_type="appointments", canonical=False)
NOTES = _stream("notes", object_type="notes", canonical=False)
MEETINGS = _stream("meetings", object_type="meetings", canonical=False)
CALLS = _stream("calls", object_type="calls", canonical=False)
EMAILS = _stream("emails", object_type="emails", canonical=False)
COMMUNICATIONS = _stream("communications", object_type="communications", canonical=False)
POSTAL_MAIL = _stream("postal_mail", object_type="postal_mail", canonical=False)

PRODUCTS = _stream("products", object_type="products", canonical=False)
LINE_ITEMS = _stream("line_items", object_type="line_items", canonical=False)
QUOTES = _stream("quotes", object_type="quotes", canonical=False)
DISCOUNTS = _stream("discounts", object_type="discounts", canonical=False)
FEES = _stream("fees", object_type="fees", canonical=False)
TAXES = _stream("taxes", object_type="taxes", canonical=False)

COURSES = _stream("courses", object_type="courses", canonical=False)
LISTINGS = _stream("listings", object_type="listings", canonical=False)
PROJECTS = _stream("projects", object_type="projects", canonical=False)
SERVICES = _stream("services", object_type="services", canonical=False)

FEEDBACK_SUBMISSIONS = _stream(
    "feedback_submissions", object_type="feedback_submissions", canonical=False
)

GOAL_TARGETS = _stream("goal_targets", object_type="goal_targets", canonical=False)
LISTS = _product_api_stream("lists", source_object="lists", primary_key="listId")
WORKFLOWS = _product_api_stream(
    "workflows",
    source_object="workflows",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/automation/v4/flows"),
)
CAMPAIGNS = _product_api_stream(
    "campaigns",
    source_object="campaigns",
    cursor_field="updatedAt",
)
MARKETING_EMAILS = _product_api_stream(
    "marketing_emails",
    source_object="marketing_emails",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/marketing/emails/2026-03"),
)
MARKETING_EVENTS = _product_api_stream(
    "marketing_events",
    source_object="marketing_events",
    primary_key="id",
    pagination=_hubspot_get_pagination("/marketing/marketing-events/2026-03"),
)
FORMS = _product_api_stream(
    "forms",
    source_object="forms",
    primary_key="id",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/marketing/v3/forms"),
)
FORM_SUBMISSIONS = _product_api_stream(
    "form_submissions",
    source_object="form_submissions",
    primary_key="id",
    cursor_field="submittedAt",
    created_at_field="submittedAt",
    updated_at_field=None,
)
CONVERSATIONS = _product_api_stream(
    "conversations",
    source_object="conversations",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/conversations/v3/conversations/threads"),
)
CONVERSATION_MESSAGES = _product_api_stream(
    "conversation_messages",
    source_object="conversation_messages",
    cursor_field="createdAt",
)
KNOWLEDGE_ARTICLES = _product_api_stream(
    "knowledge_articles",
    source_object="knowledge_articles",
)
CAMPAIGN_ASSETS = _product_api_stream(
    "campaign_assets",
    source_object="campaign_assets",
)
SITE_PAGES = _product_api_stream(
    "site_pages",
    source_object="site_pages",
    cursor_field="updated",
    created_at_field="created",
    updated_at_field="updated",
    pagination=_hubspot_get_pagination("/cms/pages/2026-03/site-pages"),
)
LANDING_PAGES = _product_api_stream(
    "landing_pages",
    source_object="landing_pages",
    cursor_field="updated",
    created_at_field="created",
    updated_at_field="updated",
    pagination=_hubspot_get_pagination("/cms/pages/2026-03/landing-pages"),
)
BLOG_POSTS = _product_api_stream(
    "blog_posts",
    source_object="blog_posts",
    cursor_field="updated",
    created_at_field="created",
    updated_at_field="updated",
    pagination=_hubspot_get_pagination("/cms/blogs/2026-03/posts"),
)
FILES = _product_api_stream(
    "files",
    source_object="files",
    cursor_field="updatedAt",
    pagination=_hubspot_get_pagination("/files/2026-03/files/search"),
)
PIPELINES = _product_api_stream("pipelines", source_object="pipelines", primary_key="id")
PIPELINE_STAGES = _product_api_stream(
    "pipeline_stages",
    source_object="pipeline_stages",
    primary_key="id",
)
ANALYTICS_VIEWS = _product_api_stream(
    "analytics_views",
    source_object="analytics_views",
    cursor_field="updated_at",
    created_at_field="created_at",
    updated_at_field="updated_at",
)
ANALYTICS_REPORTS = _product_api_stream(
    "analytics_reports",
    source_object="analytics_reports",
)

EVENT_TYPES = _product_api_stream(
    "event_types",
    source_object="event_types",
)
EVENT_OCCURRENCES = _product_api_stream(
    "event_occurrences",
    source_object="event_occurrences",
    cursor_field="occurredAt",
    created_at_field="occurredAt",
    updated_at_field=None,
)
EMAIL_EVENTS = _product_api_stream(
    "email_events",
    source_object="email_events",
    cursor_field="created",
    created_at_field="created",
    updated_at_field=None,
)

ASSOCIATION_LABELS = _product_api_stream(
    "association_labels",
    source_object="association_labels",
)
ASSOCIATIONS = _product_api_stream(
    "associations",
    source_object="associations",
)
LIST_MEMBERSHIPS = _product_api_stream(
    "list_memberships",
    source_object="list_memberships",
)
SUBSCRIPTION_DEFINITIONS = _product_api_stream(
    "subscription_definitions",
    source_object="subscription_definitions",
)
CONSENT_STATES = _product_api_stream(
    "consent_states",
    source_object="consent_states",
    cursor_field="captured_at",
    created_at_field="captured_at",
    updated_at_field=None,
)

SEQUENCES = _product_api_stream(
    "sequences",
    source_object="sequences",
    cursor_field="updatedAt",
)
SEQUENCE_ENROLLMENTS = _product_api_stream(
    "sequence_enrollments",
    source_object="sequence_enrollments",
    cursor_field="updatedAt",
)

CUSTOM_OBJECTS = _stream("custom_objects", object_type="custom_objects", canonical=False)

CARTS = _stream("carts", object_type="carts", canonical=False)
ORDERS = _stream("orders", object_type="orders", canonical=False)
SUBSCRIPTIONS = _stream("subscriptions", object_type="subscriptions", canonical=False)
INVOICES = _stream("invoices", object_type="invoices", canonical=False)
COMMERCE_PAYMENTS = _stream("commerce_payments", object_type="commerce_payments", canonical=False)

DEAL_CONTACTS = _junction("deal_contacts", parent_object="deals")
DEAL_COMPANIES = _junction("deal_companies", parent_object="deals")
TICKET_CONTACTS = _junction("ticket_contacts", parent_object="tickets")
TICKET_COMPANIES = _junction("ticket_companies", parent_object="tickets")
TASK_CONTACTS = _junction("task_contacts", parent_object="tasks")
TASK_COMPANIES = _junction("task_companies", parent_object="tasks")


ALL_STREAMS: list[StreamSpec] = [
    COMPANIES,
    CONTACTS,
    DEALS,
    TASKS,
    LEADS,
    TICKETS,
    USERS,
    OWNERS,
    OWNER_TEAMS,
    APPOINTMENTS,
    NOTES,
    MEETINGS,
    CALLS,
    EMAILS,
    COMMUNICATIONS,
    POSTAL_MAIL,
    PRODUCTS,
    LINE_ITEMS,
    QUOTES,
    DISCOUNTS,
    FEES,
    TAXES,
    COURSES,
    LISTINGS,
    PROJECTS,
    SERVICES,
    FEEDBACK_SUBMISSIONS,
    GOAL_TARGETS,
    LISTS,
    WORKFLOWS,
    CAMPAIGNS,
    MARKETING_EMAILS,
    MARKETING_EVENTS,
    FORMS,
    FORM_SUBMISSIONS,
    CONVERSATIONS,
    CONVERSATION_MESSAGES,
    KNOWLEDGE_ARTICLES,
    CAMPAIGN_ASSETS,
    SITE_PAGES,
    LANDING_PAGES,
    BLOG_POSTS,
    FILES,
    PIPELINES,
    PIPELINE_STAGES,
    ANALYTICS_VIEWS,
    ANALYTICS_REPORTS,
    EVENT_TYPES,
    EVENT_OCCURRENCES,
    EMAIL_EVENTS,
    ASSOCIATION_LABELS,
    ASSOCIATIONS,
    LIST_MEMBERSHIPS,
    SUBSCRIPTION_DEFINITIONS,
    CONSENT_STATES,
    SEQUENCES,
    SEQUENCE_ENROLLMENTS,
    CUSTOM_OBJECTS,
    CARTS,
    ORDERS,
    SUBSCRIPTIONS,
    INVOICES,
    COMMERCE_PAYMENTS,
    DEAL_CONTACTS,
    DEAL_COMPANIES,
    TICKET_CONTACTS,
    TICKET_COMPANIES,
    TASK_CONTACTS,
    TASK_COMPANIES,
]


class HubSpotConnector(RestConnector):
    name = "hubspot"
    base_url = "https://api.hubapi.com"
    streams_list = ALL_STREAMS

    @staticmethod
    def _build_search_body(
        stream: StreamSpec,
        properties: list[str],
        cursor: str | None,
        after: str | None,
    ) -> dict[str, Any]:
        """Search body — order ascending by the cursor field, filter on `>=`, page via `after`.
        `properties` is everything the account exposes for the object; HubSpot's search only returns
        properties named here, so pass them all."""
        body: dict[str, Any] = {
            "properties": properties,
            "limit": PAGE_LIMIT,
            "sorts": [
                {"propertyName": stream.cursor_field, "direction": "ASCENDING"},
            ],
        }
        if cursor and stream.cursor_field:
            body["filterGroups"] = [
                {
                    "filters": [
                        {
                            "propertyName": stream.cursor_field,
                            "operator": "GTE",
                            "value": cursor,
                        }
                    ]
                }
            ]
        if after:
            body["after"] = after
        return body

    @staticmethod
    def _flatten(record: dict[str, Any]) -> dict[str, Any]:
        """Lift `properties.*` to top-level alongside id / timestamps."""
        flat: dict[str, Any] = {
            "id": record.get("id"),
            "createdAt": record.get("createdAt"),
            "updatedAt": record.get("updatedAt"),
            "archived": record.get("archived", False),
        }
        props = record.get("properties") or {}
        if isinstance(props, dict):
            flat.update(props)
        return flat

    @staticmethod
    def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Normalize non-CRM product-API rows: most already return top-level fields; campaigns use a
        CRM-like `properties` bag and form submissions return `values[]` name/value pairs, so lift
        both shapes into the flat record."""
        flat = dict(record)
        object_id = flat.get("objectId")
        if stream.name != "event_occurrences" and object_id is not None and not flat.get("id"):
            flat["id"] = str(object_id)
        props = flat.get("properties")
        if isinstance(props, dict):
            flat.update(props)
        values = flat.get("values")
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str):
                    flat[name] = item.get("value")
        if stream.name == "knowledge_articles":
            flat["publishedDate"] = _normalize_epoch_millis(flat.get("publishedDate"))
        if stream.name == "email_events":
            flat["createdAt"] = _normalize_epoch_millis(flat.get("created"))
        return flat

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name in _JUNCTION_STREAMS:
            return record
        if stream.name == _CUSTOM_OBJECTS_STREAM:
            return record
        if stream.name in _PASSTHROUGH_STREAMS:
            return self._flatten_product_api(record, stream)
        return self._flatten(record)

    def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != _CONSENT_STATES_STREAM:
            return super().record_identity(record, stream)
        contact_id = record.get("contact_id")
        subscription_id = record.get("subscriptionId")
        status_kind = record.get("wideStatusType")
        if status_kind is None and record.get("purpose") == "unsubscribe_all":
            status_kind = "unsubscribe_all"
        suffix = subscription_id if subscription_id is not None else status_kind
        business_unit_id = record.get("businessUnitId")
        business_unit = business_unit_id if business_unit_id is not None else "default"
        if contact_id is None or suffix is None:
            return None
        return f"{contact_id}:{suffix}:{business_unit}"

    async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]:
        """Every property HubSpot exposes for an object type. The names go straight into the search
        request body so the sync lands every available field — no hand-listing anywhere."""
        data = await self._get(client, f"/crm/v3/properties/{source_object}")
        return [
            str(p["name"])
            for p in (data.get("results") or [])
            if isinstance(p, dict) and p.get("name")
        ]

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            async for page in self._paginate_unchecked(client, stream, cursor=cursor):
                yield page
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 or self._is_stream_unavailable(e):
                raise StreamSkipped(self._stream_skip_reason(stream.name, e)) from e
            raise

    async def _paginate_unchecked(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.pagination is not None:
            async for strategy_page in self.paginate_from_strategy(stream, client=client):
                yield strategy_page
            return
        if stream.name in _JUNCTION_STREAMS:
            parent_obj, target_obj = _JUNCTION_STREAMS[stream.name]
            async for junction_page in self._paginate_junction(
                client, parent_object=parent_obj, target_object=target_obj
            ):
                yield junction_page
            return
        if stream.name == _CUSTOM_OBJECTS_STREAM:
            async for custom_page in self._paginate_custom_objects(client, cursor=cursor):
                yield custom_page
            return
        if stream.name in _PRODUCT_API_STREAMS:
            async for product_page in self._paginate_product_api(client, stream, cursor=cursor):
                yield product_page
            return
        async for page in self._paginate_crm_object(client, stream, cursor=cursor):
            yield page

    async def _paginate_crm_object(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        path = f"/crm/v3/objects/{stream.source_object}/search"
        properties = await self._list_properties(client, stream.source_object)
        boundary_ids: set[str] = set()
        after: str | None = None
        while True:
            body = self._build_search_body(stream, properties, cursor, after)
            data = await self._post(client, path, json=body)
            results = data.get("results", []) or []
            page: list[dict[str, Any]] = []
            for r in results:
                rid = r.get("id")
                cur_val = (r.get("properties") or {}).get(stream.cursor_field)
                if cursor and cur_val == cursor and rid in boundary_ids:
                    continue
                if cur_val == cursor and isinstance(rid, str):
                    boundary_ids.add(rid)
                page.append(r)
            if page:
                yield page
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break

        async for archived_page in self._paginate_archived_ids(client, stream):
            yield archived_page

    @staticmethod
    def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool:
        """True when HubSpot says this account cannot read one object — a per-stream capability fact
        (product tier or granular OAuth scope), not a connection failure, so the runner records it
        as a skipped stream."""
        if exc.response.status_code != 403:
            return False
        try:
            body = exc.response.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False
        message = str(body.get("message") or "").lower()
        return any(
            marker in message
            for marker in (
                "permissions to view object type",
                "requires one of",
                "requires all of",
                "does not have proper permissions",
                "required scopes",
                "missing scopes",
            )
        )

    @staticmethod
    def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str:
        try:
            body = exc.response.json()
        except ValueError:
            body = {}
        message = body.get("message") if isinstance(body, dict) else None
        return (
            f"HubSpot stream {stream_name!r} unavailable for this account "
            f"({message or 'see upstream response'})"
        )

    async def _paginate_archived_ids(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
    ) -> AsyncIterator[StreamPage]:
        """The search API omits archived records, so every object stream follows its search with a
        cheap archived-id sweep against the list endpoint, tombstoning what HubSpot deleted."""
        after: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_LIMIT,
                "archived": "true",
                "properties": "hs_object_id",
            }
            if after:
                params["after"] = after
            try:
                data = await self._get(
                    client,
                    f"/crm/v3/objects/{stream.source_object}",
                    params=params,
                )
            except httpx.HTTPStatusError as e:
                if self._is_archived_sweep_unsupported(e) or self._is_stream_unavailable(e):
                    return
                raise
            deletes = [
                str(record["id"])
                for record in data.get("results", []) or []
                if isinstance(record, dict) and record.get("id")
            ]
            if deletes:
                yield StreamPage(deletes=tuple(deletes))
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    async def _paginate_product_api(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._product_pages(client, stream.name, cursor):
            yield page

    def _product_pages(
        self, client: httpx.AsyncClient, name: str, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        paginators: dict[
            str, Callable[[httpx.AsyncClient], AsyncIterator[list[dict[str, Any]]]]
        ] = {
            "owner_teams": self._paginate_owner_teams,
            "lists": self._paginate_lists,
            "knowledge_articles": partial(
                self._paginate_site_search, content_type="KNOWLEDGE_ARTICLE"
            ),
            "campaign_assets": self._paginate_campaign_assets,
            "analytics_views": self._paginate_analytics_views,
            "analytics_reports": self._paginate_analytics_reports,
            "event_types": self._paginate_event_types,
            "event_occurrences": partial(self._paginate_event_occurrences, cursor=cursor),
            "email_events": partial(self._paginate_email_events, cursor=cursor),
            _ASSOCIATION_LABELS_STREAM: self._paginate_association_labels,
            _ASSOCIATIONS_STREAM: self._paginate_associations,
            _LIST_MEMBERSHIPS_STREAM: self._paginate_list_memberships,
            _SUBSCRIPTION_DEFINITIONS_STREAM: self._paginate_subscription_definitions,
            _CONSENT_STATES_STREAM: self._paginate_consent_states,
            _SEQUENCES_STREAM: self._paginate_sequences,
            _SEQUENCE_ENROLLMENTS_STREAM: self._paginate_sequence_enrollments,
            "form_submissions": self._paginate_form_submissions,
            "conversation_messages": self._paginate_conversation_messages,
            "pipelines": self._paginate_pipelines,
            "pipeline_stages": self._paginate_pipeline_stages,
        }
        paginator = paginators.get(name)
        if paginator is not None:
            return paginator(client)
        path = _GET_PRODUCT_API_PATHS.get(name)
        if path is None:
            raise NotImplementedError(f"hubspot: product API stream {name!r} has no path")
        return self._paginate_get_collection(client, path)

    async def _paginate_get_collection(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        limit: int = PAGE_LIMIT,
        extra_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if extra_params:
                params.update(extra_params)
            if after:
                params["after"] = after
            data = await self._get(client, path, params=params)
            results: list[dict[str, Any]] = []
            for record in data.get("results", []) or []:
                if not isinstance(record, dict):
                    continue
                object_id = record.get("objectId")
                if object_id is not None and not record.get("id"):
                    record = {**record, "id": str(object_id)}
                results.append(record)
            if results:
                yield results
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    async def _paginate_custom_objects(
        self,
        client: httpx.AsyncClient,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        schemas = await self._custom_object_schemas(client)
        for schema in schemas:
            object_type_id = self._schema_object_type_id(schema)
            if object_type_id is None:
                continue
            properties = self._schema_property_names(schema)
            if "hs_lastmodifieddate" not in properties:
                properties.append("hs_lastmodifieddate")
            search_stream = StreamSpec(
                name=_CUSTOM_OBJECTS_STREAM,
                source_object=object_type_id,
                primary_key="id",
                cursor_field="hs_lastmodifieddate",
                created_at_field="createdAt",
                updated_at_field="hs_lastmodifieddate",
                canonical=False,
            )
            async for page in self._paginate_custom_object_records(
                client,
                search_stream,
                schema=schema,
                properties=properties,
                cursor=cursor,
            ):
                yield page
            async for archived_page in self._paginate_custom_object_archived_ids(
                client,
                object_type_id=object_type_id,
            ):
                yield archived_page

    async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, _CUSTOM_OBJECT_SCHEMA_PATH)
        return [row for row in data.get("results", []) or [] if isinstance(row, dict)]

    @staticmethod
    def _schema_object_type_id(schema: dict[str, Any]) -> str | None:
        for key in ("objectTypeId", "fullyQualifiedName", "name"):
            value = schema.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _schema_property_names(schema: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for prop in schema.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            name = prop.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        for key in ("primaryDisplayProperty",):
            value = schema.get(key)
            if isinstance(value, str) and value and value not in names:
                names.append(value)
        for value in schema.get("secondaryDisplayProperties") or []:
            if isinstance(value, str) and value and value not in names:
                names.append(value)
        return names

    async def _paginate_custom_object_records(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        schema: dict[str, Any],
        properties: list[str],
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        after: str | None = None
        boundary_ids: set[str] = set()
        while True:
            body = self._build_search_body(stream, properties, cursor, after)
            data = await self._post(
                client, f"/crm/v3/objects/{stream.source_object}/search", json=body
            )
            rows: list[dict[str, Any]] = []
            for record in data.get("results", []) or []:
                if not isinstance(record, dict):
                    continue
                rid = record.get("id")
                raw_props = record.get("properties")
                props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
                cur_val = props.get(stream.cursor_field) if stream.cursor_field else None
                if cursor and cur_val == cursor and rid in boundary_ids:
                    continue
                if cur_val == cursor and isinstance(rid, str):
                    boundary_ids.add(rid)
                row = self._custom_object_row(record, schema=schema)
                if row is not None:
                    rows.append(row)
            if rows:
                yield rows
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    def _custom_object_row(
        self,
        record: dict[str, Any],
        *,
        schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        object_type_id = self._schema_object_type_id(schema)
        record_id = record.get("id")
        if object_type_id is None or record_id is None:
            return None
        raw_props = record.get("properties")
        props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        raw_labels = schema.get("labels")
        labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
        primary = schema.get("primaryDisplayProperty")
        secondary = [
            value
            for value in (schema.get("secondaryDisplayProperties") or [])
            if isinstance(value, str)
        ]
        title = props.get(primary) if isinstance(primary, str) else None
        secondary_values = [
            str(props[name]) for name in secondary if props.get(name) not in (None, "")
        ]
        return {
            **props,
            "id": f"{object_type_id}:{record_id}",
            "record_id": str(record_id),
            "object_type_id": object_type_id,
            "object_name": schema.get("name"),
            "object_label": labels.get("singular") or labels.get("plural") or schema.get("name"),
            "title": title or str(record_id),
            "secondary_title": " · ".join(secondary_values) if secondary_values else None,
            "primary_display_property": primary,
            "secondary_display_properties": secondary,
            "properties": props,
            "createdAt": record.get("createdAt"),
            "updatedAt": record.get("updatedAt"),
            "archived": record.get("archived", False),
        }

    async def _paginate_custom_object_archived_ids(
        self,
        client: httpx.AsyncClient,
        *,
        object_type_id: str,
    ) -> AsyncIterator[StreamPage]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_LIMIT,
                "archived": "true",
                "properties": "hs_object_id",
            }
            if after:
                params["after"] = after
            try:
                data = await self._get(client, f"/crm/v3/objects/{object_type_id}", params=params)
            except httpx.HTTPStatusError as e:
                if self._is_archived_sweep_unsupported(e) or self._is_stream_unavailable(e):
                    return
                raise
            deletes = [
                f"{object_type_id}:{record['id']}"
                for record in data.get("results", []) or []
                if isinstance(record, dict) and record.get("id")
            ]
            if deletes:
                yield StreamPage(deletes=tuple(deletes))
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    async def _paginate_owner_teams(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        teams: dict[str, dict[str, Any]] = {}
        async for owners_page in self._paginate_get_collection(client, "/crm/v3/owners"):
            for owner in owners_page:
                for team in owner.get("teams") or []:
                    if not isinstance(team, dict):
                        continue
                    team_id = team.get("id")
                    if team_id is None:
                        continue
                    team_id_str = str(team_id)
                    teams[team_id_str] = {
                        "id": team_id_str,
                        "name": team.get("name") or team_id_str,
                        "primary": team.get("primary"),
                    }
        if teams:
            yield list(teams.values())

    async def _paginate_lists(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        offset = 0
        while True:
            data = await self._post(
                client,
                "/crm/lists/2026-03/search",
                json={"count": 500, "offset": offset},
            )
            page: list[dict[str, Any]] = []
            for record in data.get("lists", []) or []:
                if not isinstance(record, dict):
                    continue
                list_id = record.get("listId")
                if list_id is not None:
                    record = {**record, "id": str(list_id)}
                additional = record.get("additionalProperties")
                if isinstance(additional, dict):
                    record = {**record, **additional}
                page.append(record)
            if page:
                yield page
            if not data.get("hasMore"):
                return
            next_offset = data.get("offset")
            if isinstance(next_offset, int) and next_offset != offset:
                offset = next_offset
            else:
                offset += len(page)

    async def _paginate_site_search(
        self,
        client: httpx.AsyncClient,
        *,
        content_type: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        offset = 0
        while True:
            data = await self._get(
                client,
                "/cms/v3/site-search/search",
                params={"type": content_type, "limit": PAGE_LIMIT, "offset": offset},
            )
            page = [row for row in data.get("results", []) or [] if isinstance(row, dict)]
            if page:
                yield page
            next_offset = data.get("offset", offset) + data.get("limit", PAGE_LIMIT)
            total = data.get("total")
            if not isinstance(total, int) or next_offset >= total:
                return
            offset = next_offset

    async def _paginate_campaign_assets(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for campaigns_page in self._paginate_get_collection(
            client,
            _GET_PRODUCT_API_PATHS["campaigns"],
        ):
            for campaign in campaigns_page:
                campaign_id = campaign.get("id") or campaign.get("campaignGuid")
                if not campaign_id:
                    continue
                campaign_name = (
                    campaign.get("name")
                    or campaign.get("hs_name")
                    or (campaign.get("properties") or {}).get("hs_name")
                )
                for asset_type in _CAMPAIGN_ASSET_TYPES:
                    async for page in self._paginate_campaign_asset_type(
                        client,
                        campaign_id=str(campaign_id),
                        campaign_name=campaign_name,
                        asset_type=asset_type,
                    ):
                        yield page

    async def _paginate_campaign_asset_type(
        self,
        client: httpx.AsyncClient,
        *,
        campaign_id: str,
        campaign_name: Any,
        asset_type: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/marketing/campaigns/2026-03/{campaign_id}/assets/{asset_type}"
        try:
            async for assets in self._paginate_get_collection(client, path):
                page: list[dict[str, Any]] = []
                for asset in assets:
                    asset_id = asset.get("id") or asset.get("assetId")
                    if asset_id is None:
                        continue
                    page.append(
                        {
                            **asset,
                            "id": f"{campaign_id}:{asset_type}:{asset_id}",
                            "asset_id": str(asset_id),
                            "asset_type": asset_type,
                            "asset_kind": _MARKETING_ASSET_KIND_BY_TYPE.get(
                                asset_type, asset_type.lower()
                            ),
                            "campaign_id": campaign_id,
                            "campaign_name": campaign_name,
                            "metrics": asset.get("metrics") or {},
                        }
                    )
                if page:
                    yield page
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {403, 404}:
                return
            raise

    async def _paginate_analytics_views(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = await self._analytics_view_rows(client)
        if page:
            yield page

    async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/analytics/v2/views")
        rows = data if isinstance(data, list) else data.get("results", [])
        page: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id") or row.get("viewId") or row.get("name")
            if row_id is None:
                continue
            filters = (
                row.get("filters")
                or row.get("propertyFilters")
                or row.get("reportPropertyFilters")
                or row.get("filter")
            )
            page.append(
                {
                    **row,
                    "id": str(row_id),
                    "name": row.get("name") or row.get("title") or row.get("label") or str(row_id),
                    "report_kind": "analytics_view",
                    "filters": filters or {},
                    "created_at": _normalize_epoch_millis(
                        row.get("createdAt") or row.get("created")
                    ),
                    "updated_at": row.get("updatedDate")
                    or row.get("updatedAt")
                    or row.get("updated"),
                }
            )
        return page

    async def _paginate_analytics_reports(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        start_date, end_date = self._analytics_report_window()
        analytics_views = await self._analytics_view_rows(client)
        view_filters: list[tuple[str | None, str | None]] = [(None, None)]
        view_filters.extend(
            (str(row["id"]), str(row["name"]))
            for row in analytics_views
            if row.get("id") is not None and row.get("name") is not None
        )

        for family, subjects in (
            ("breakdown", _ANALYTICS_REPORT_BREAKDOWN_TYPES),
            ("object", _ANALYTICS_REPORT_OBJECT_TYPES),
        ):
            for subject in subjects:
                for time_period in _ANALYTICS_REPORT_TIME_PERIODS:
                    for analytics_view_id, analytics_view_name in view_filters:
                        async for page in self._paginate_analytics_report_query(
                            client,
                            family=family,
                            subject=subject,
                            time_period=time_period,
                            analytics_view_id=analytics_view_id,
                            analytics_view_name=analytics_view_name,
                            start_date=start_date,
                            end_date=end_date,
                        ):
                            yield page

    @staticmethod
    def _analytics_report_window() -> tuple[str, str]:
        return _ANALYTICS_REPORT_START, datetime.now(UTC).strftime("%Y%m%d")

    async def _paginate_analytics_report_query(
        self,
        client: httpx.AsyncClient,
        *,
        family: str,
        subject: str,
        time_period: str,
        analytics_view_id: str | None,
        analytics_view_name: str | None,
        start_date: str,
        end_date: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        offset = 0
        while True:
            params: dict[str, Any] = {
                "start": start_date,
                "end": end_date,
                "limit": _ANALYTICS_REPORT_LIMIT,
                "offset": offset,
            }
            if analytics_view_id is not None:
                params["filterId"] = analytics_view_id
            try:
                data = await self._get(
                    client,
                    f"/analytics/v2/reports/{subject}/{time_period}",
                    params=params,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code in {400, 404}:
                    return
                raise

            rows = self._analytics_report_rows(
                data,
                family=family,
                subject=subject,
                time_period=time_period,
                analytics_view_id=analytics_view_id,
                analytics_view_name=analytics_view_name,
                start_date=start_date,
                end_date=end_date,
                offset=offset,
            )
            if rows:
                yield rows
            breakdowns = [row for row in data.get("breakdowns", []) or [] if isinstance(row, dict)]
            total = data.get("total")
            next_offset = offset + len(breakdowns)
            if not isinstance(total, int) or next_offset >= total or not breakdowns:
                return
            offset = next_offset

    @staticmethod
    def _analytics_report_rows(
        data: dict[str, Any],
        *,
        family: str,
        subject: str,
        time_period: str,
        analytics_view_id: str | None,
        analytics_view_name: str | None,
        start_date: str,
        end_date: str,
        offset: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        view_part = analytics_view_id or "all"
        filters = {"filterId": analytics_view_id} if analytics_view_id is not None else {}
        if offset == 0 and isinstance(data.get("totals"), dict):
            metrics = dict(data["totals"])
            rows.append(
                {
                    **metrics,
                    "id": HubSpotConnector._analytics_report_id(
                        family,
                        subject,
                        time_period,
                        view_part,
                        "totals",
                        None,
                    ),
                    "name": f"{subject} {time_period} totals",
                    "report_kind": "analytics_report_totals",
                    "report_family": family,
                    "report_subject": subject,
                    "time_period": time_period,
                    "analytics_view_id": analytics_view_id,
                    "analytics_view_name": analytics_view_name,
                    "breakdown": None,
                    "metrics": metrics,
                    "filters": filters,
                    "start_date": HubSpotConnector._analytics_report_date(start_date),
                    "end_date": HubSpotConnector._analytics_report_date(end_date),
                }
            )

        for idx, breakdown in enumerate(data.get("breakdowns", []) or []):
            if not isinstance(breakdown, dict):
                continue
            metrics = {
                key: value for key, value in breakdown.items() if key not in {"breakdown", "meta"}
            }
            breakdown_value = breakdown.get("breakdown") or breakdown.get("meta") or offset + idx
            rows.append(
                {
                    **breakdown,
                    "id": HubSpotConnector._analytics_report_id(
                        family,
                        subject,
                        time_period,
                        view_part,
                        "breakdown",
                        breakdown_value,
                    ),
                    "name": f"{subject} {time_period}: {breakdown_value}",
                    "report_kind": "analytics_report_breakdown",
                    "report_family": family,
                    "report_subject": subject,
                    "time_period": time_period,
                    "analytics_view_id": analytics_view_id,
                    "analytics_view_name": analytics_view_name,
                    "breakdown": str(breakdown_value),
                    "metrics": metrics,
                    "filters": filters,
                    "start_date": HubSpotConnector._analytics_report_date(start_date),
                    "end_date": HubSpotConnector._analytics_report_date(end_date),
                }
            )
        return rows

    @staticmethod
    def _analytics_report_id(*parts: Any) -> str:
        return "analytics_report:" + ":".join(
            str(part).replace("/", "-").replace(":", "_") if part is not None else "none"
            for part in parts
        )

    @staticmethod
    def _analytics_report_date(value: str) -> str:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    async def _paginate_event_types(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        data = await self._get(client, "/events/v3/events/event-types")
        raw_rows = data if isinstance(data, list) else data.get("results", [])
        rows: list[dict[str, Any]] = []
        for row in raw_rows or []:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id") or row.get("fullyQualifiedName")
            rows.append(row if row_id is None else {**row, "id": str(row_id)})
        if rows:
            yield rows

    async def _paginate_event_occurrences(
        self,
        client: httpx.AsyncClient,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params: dict[str, Any] = {}
        if cursor:
            params["occurredAfter"] = cursor
        try:
            data = await self._get(client, "/events/v3/events", params=params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return
            raise
        rows = [row for row in data.get("results", []) or [] if isinstance(row, dict)]
        if rows:
            yield rows

    async def _paginate_email_events(
        self,
        client: httpx.AsyncClient,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        offset: str | None = None
        while True:
            params: dict[str, Any] = {"limit": _EMAIL_EVENT_LIMIT}
            start_timestamp = self._email_event_start_timestamp(cursor)
            if start_timestamp is not None:
                params["startTimestamp"] = start_timestamp
            if offset:
                params["offset"] = offset
            data = await self._get(client, "/email/public/v1/events", params=params)
            rows = [row for row in data.get("events", []) or [] if isinstance(row, dict)]
            if rows:
                yield rows
            if not data.get("hasMore"):
                return
            next_offset = data.get("offset")
            if next_offset is None or str(next_offset) == offset:
                return
            offset = str(next_offset)

    @staticmethod
    def _email_event_start_timestamp(cursor: str | None) -> int | None:
        if not cursor:
            return None
        if cursor.isdigit():
            return int(cursor)
        try:
            parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(parsed.timestamp() * 1000)

    async def _paginate_association_labels(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for from_object_type, to_object_type, labels in self._association_pairs_with_labels(
            client
        ):
            page = [
                self._association_label_row(
                    label,
                    from_object_type=from_object_type,
                    to_object_type=to_object_type,
                )
                for label in labels
            ]
            if page:
                yield page

    async def _paginate_associations(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for from_object_type, to_object_type, _labels in self._association_pairs_with_labels(
            client
        ):
            async for id_page in self._paginate_crm_object_id_pages(client, from_object_type):
                inputs = [{"id": record_id} for record_id in id_page]
                async for page in self._paginate_association_batch(
                    client,
                    from_object_type=from_object_type,
                    to_object_type=to_object_type,
                    inputs=inputs,
                ):
                    yield page

    async def _paginate_association_batch(
        self,
        client: httpx.AsyncClient,
        *,
        from_object_type: str,
        to_object_type: str,
        inputs: list[dict[str, str]],
    ) -> AsyncIterator[list[dict[str, Any]]]:
        pending = inputs
        while pending:
            try:
                data = await self._post(
                    client,
                    (f"/crm/associations/2026-03/{from_object_type}/{to_object_type}/batch/read"),
                    json={"inputs": pending},
                )
            except httpx.HTTPStatusError as e:
                if self._is_optional_pair_unavailable(e):
                    return
                raise
            page = self._association_rows(
                data,
                from_object_type=from_object_type,
                to_object_type=to_object_type,
            )
            if page:
                yield page
            pending = self._next_association_inputs(data)

    @staticmethod
    def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]:
        inputs: list[dict[str, str]] = []
        for result in data.get("results", []) or []:
            if not isinstance(result, dict):
                continue
            raw_from = result.get("from")
            from_record_id = raw_from.get("id") if isinstance(raw_from, dict) else None
            after = (
                ((result.get("paging") or {}).get("next") or {}).get("after")
                if isinstance(result.get("paging"), dict)
                else None
            )
            if from_record_id is not None and after is not None:
                inputs.append({"id": str(from_record_id), "after": str(after)})
        return inputs

    async def _association_pairs_with_labels(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]:
        object_types = await self._association_object_types(client)
        for from_object_type in object_types:
            for to_object_type in object_types:
                labels = await self._association_labels_for_pair(
                    client,
                    from_object_type=from_object_type,
                    to_object_type=to_object_type,
                )
                if labels:
                    yield from_object_type, to_object_type, labels

    async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]:
        object_types = list(_ASSOCIATION_STANDARD_OBJECT_TYPES)
        try:
            schemas = await self._custom_object_schemas(client)
        except httpx.HTTPStatusError as e:
            if self._is_optional_pair_unavailable(e):
                schemas = []
            else:
                raise
        for schema in schemas:
            object_type_id = self._schema_object_type_id(schema)
            if object_type_id is not None and object_type_id not in object_types:
                object_types.append(object_type_id)
        return object_types

    async def _association_labels_for_pair(
        self,
        client: httpx.AsyncClient,
        *,
        from_object_type: str,
        to_object_type: str,
    ) -> list[dict[str, Any]]:
        try:
            data = await self._get(
                client,
                (f"/crm/associations/2026-03/{from_object_type}/{to_object_type}/labels"),
            )
        except httpx.HTTPStatusError as e:
            if self._is_optional_pair_unavailable(e):
                return []
            raise
        return [row for row in data.get("results", []) or [] if isinstance(row, dict)]

    @staticmethod
    def _association_label_row(
        label: dict[str, Any],
        *,
        from_object_type: str,
        to_object_type: str,
    ) -> dict[str, Any]:
        type_id = label.get("typeId") or label.get("associationTypeId")
        category = label.get("category") or label.get("associationCategory")
        display_label = label.get("label")
        return {
            **label,
            "id": f"{from_object_type}:{to_object_type}:{category or 'default'}:{type_id}",
            "from_object_type": from_object_type,
            "to_object_type": to_object_type,
            "association_type_id": str(type_id) if type_id is not None else None,
            "association_category": category,
            "association_label": display_label,
        }

    async def _paginate_crm_object_id_pages(
        self,
        client: httpx.AsyncClient,
        object_type: str,
    ) -> AsyncIterator[list[str]]:
        async for records in self._paginate_crm_object_pages(
            client,
            object_type,
            properties=("hs_object_id",),
        ):
            ids = [str(record["id"]) for record in records if record.get("id") is not None]
            if ids:
                yield ids

    async def _paginate_crm_object_pages(
        self,
        client: httpx.AsyncClient,
        object_type: str,
        *,
        properties: tuple[str, ...],
    ) -> AsyncIterator[list[dict[str, Any]]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_LIMIT,
                "properties": ",".join(properties),
            }
            if after:
                params["after"] = after
            try:
                data = await self._get(
                    client,
                    f"/crm/v3/objects/{object_type}",
                    params=params,
                )
            except httpx.HTTPStatusError as e:
                if self._is_optional_pair_unavailable(e):
                    return
                raise
            page = [row for row in data.get("results", []) or [] if isinstance(row, dict)]
            if page:
                yield page
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    @staticmethod
    def _association_rows(
        data: dict[str, Any],
        *,
        from_object_type: str,
        to_object_type: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for result in data.get("results", []) or []:
            if not isinstance(result, dict):
                continue
            raw_from = result.get("from")
            from_record_id = raw_from.get("id") if isinstance(raw_from, dict) else None
            if from_record_id is None:
                continue
            for to_record in result.get("to", []) or []:
                if not isinstance(to_record, dict):
                    continue
                to_record_id = to_record.get("toObjectId") or to_record.get("id")
                if to_record_id is None:
                    continue
                association_types = to_record.get("associationTypes") or [None]
                for idx, association_type in enumerate(association_types):
                    if association_type is not None and not isinstance(association_type, dict):
                        continue
                    rows.append(
                        HubSpotConnector._association_row(
                            association_type or {},
                            from_object_type=from_object_type,
                            from_record_id=str(from_record_id),
                            to_object_type=to_object_type,
                            to_record_id=str(to_record_id),
                            fallback_idx=idx,
                        )
                    )
        return rows

    @staticmethod
    def _association_row(
        association_type: dict[str, Any],
        *,
        from_object_type: str,
        from_record_id: str,
        to_object_type: str,
        to_record_id: str,
        fallback_idx: int,
    ) -> dict[str, Any]:
        type_id = association_type.get("typeId") or association_type.get("associationTypeId")
        category = association_type.get("category") or association_type.get("associationCategory")
        label = association_type.get("label")
        type_part = type_id if type_id is not None else fallback_idx
        return {
            "id": (
                f"{from_object_type}:{from_record_id}:"
                f"{to_object_type}:{to_record_id}:{category or 'default'}:{type_part}"
            ),
            "from_object_type": from_object_type,
            "from_record_id": from_record_id,
            "to_object_type": to_object_type,
            "to_record_id": to_record_id,
            "association_type_id": str(type_id) if type_id is not None else None,
            "association_category": category,
            "association_label": label,
            "relationship_type": label or (str(type_id) if type_id is not None else None),
            "association_types": [association_type] if association_type else [],
        }

    @staticmethod
    def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool:
        if exc.response.status_code in {400, 404}:
            return True
        return HubSpotConnector._is_stream_unavailable(exc)

    async def _paginate_list_memberships(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for lists_page in self._paginate_lists(client):
            for list_record in lists_page:
                list_id = list_record.get("listId") or list_record.get("id")
                if list_id is None:
                    continue
                async for memberships in self._paginate_memberships_for_list(
                    client,
                    list_record=list_record,
                    list_id=str(list_id),
                ):
                    yield memberships

    async def _paginate_memberships_for_list(
        self,
        client: httpx.AsyncClient,
        *,
        list_record: dict[str, Any],
        list_id: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_LIMIT}
            if after:
                params["after"] = after
            try:
                data = await self._get(
                    client,
                    f"/crm/lists/2026-03/{list_id}/memberships",
                    params=params,
                )
            except httpx.HTTPStatusError as e:
                if self._is_optional_pair_unavailable(e):
                    return
                raise
            page: list[dict[str, Any]] = []
            for row in data.get("results", []) or []:
                if not isinstance(row, dict):
                    continue
                record_id = row.get("recordId")
                if record_id is None:
                    continue
                page.append(
                    {
                        **row,
                        "id": f"{list_id}:{record_id}",
                        "list_id": list_id,
                        "list_name": list_record.get("name"),
                        "object_type_id": list_record.get("objectTypeId"),
                        "processingType": list_record.get("processingType"),
                    }
                )
            if page:
                yield page
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    async def _paginate_subscription_definitions(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        data = await self._get(client, "/communication-preferences/2026-03/definitions")
        raw_rows = data.get("results") or data.get("subscriptionDefinitions") or []
        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_rows):
            if not isinstance(row, dict):
                continue
            row_id = row.get("id") or row.get("subscriptionId") or idx
            rows.append({**row, "id": str(row_id)})
        if rows:
            yield rows

    async def _paginate_consent_states(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for contacts in self._paginate_contact_identity_pages(client):
            page: list[dict[str, Any]] = []
            for contact in contacts:
                email = contact.get("email")
                if not isinstance(email, str) or not email:
                    continue
                page.extend(await self._consent_status_rows(client, contact=contact, email=email))
                page.extend(await self._unsubscribe_all_rows(client, contact=contact, email=email))
            if page:
                yield page

    async def _paginate_contact_identity_pages(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for records in self._paginate_crm_object_pages(
            client,
            "contacts",
            properties=("email",),
        ):
            page: list[dict[str, Any]] = []
            for record in records:
                props = record.get("properties")
                properties = props if isinstance(props, dict) else {}
                email = record.get("email") or properties.get("email")
                page.append({**record, "email": email})
            if page:
                yield page

    async def _consent_status_rows(
        self,
        client: httpx.AsyncClient,
        *,
        contact: dict[str, Any],
        email: str,
    ) -> list[dict[str, Any]]:
        try:
            data = await self._get(
                client,
                f"/communication-preferences/2026-03/statuses/{quote(email, safe='')}",
                params={"channel": "EMAIL"},
            )
        except httpx.HTTPStatusError as e:
            if self._is_optional_pair_unavailable(e):
                return []
            raise
        return [
            self._consent_row(row, contact=contact, email=email, status_kind="subscription")
            for row in data.get("results", []) or []
            if isinstance(row, dict)
        ]

    async def _unsubscribe_all_rows(
        self,
        client: httpx.AsyncClient,
        *,
        contact: dict[str, Any],
        email: str,
    ) -> list[dict[str, Any]]:
        try:
            data = await self._get(
                client,
                (
                    "/communication-preferences/2026-03/statuses/"
                    f"{quote(email, safe='')}/unsubscribe-all"
                ),
                params={"channel": "EMAIL"},
            )
        except httpx.HTTPStatusError as e:
            if self._is_optional_pair_unavailable(e):
                return []
            raise
        return [
            self._consent_row(row, contact=contact, email=email, status_kind="unsubscribe_all")
            for row in data.get("results", []) or []
            if isinstance(row, dict)
        ]

    @staticmethod
    def _consent_row(
        row: dict[str, Any],
        *,
        contact: dict[str, Any],
        email: str,
        status_kind: str,
    ) -> dict[str, Any]:
        subscription_id = row.get("subscriptionId")
        business_unit_id = row.get("businessUnitId")
        suffix = subscription_id if subscription_id is not None else status_kind
        business_unit_part = business_unit_id if business_unit_id is not None else "default"
        contact_id = str(contact["id"]) if contact.get("id") is not None else None
        subscription_name = row.get("subscriptionName")
        purpose = row.get("purpose") or (
            "unsubscribe_all" if status_kind == "unsubscribe_all" else subscription_name
        )
        timestamp = row.get("timestamp")
        return {
            **row,
            "id": f"{email}:{suffix}:{business_unit_part}",
            "contact_id": contact_id,
            "subject_email": email,
            "purpose": purpose,
            "subscription_type": subscription_name
            or row.get("wideStatusType")
            or (str(subscription_id) if subscription_id is not None else status_kind),
            "status": row.get("status"),
            "legal_basis": row.get("legalBasis"),
            "source": row.get("source") or status_kind,
            "captured_at": timestamp,
            "created_at": timestamp,
        }

    async def _paginate_sequences(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for users in self._sequence_user_rows(client):
            for user in users:
                user_id = user["user_id"]
                try:
                    async for rows in self._paginate_get_collection(
                        client,
                        "/automation/sequences/2026-03",
                        extra_params={"userId": user_id},
                    ):
                        page = [
                            {
                                **row,
                                "userId": row.get("userId") or user_id,
                                "owner_id": user.get("owner_id"),
                                "owner_email": user.get("owner_email"),
                            }
                            for row in rows
                        ]
                        if page:
                            yield page
                except httpx.HTTPStatusError as e:
                    if self._is_optional_pair_unavailable(e):
                        continue
                    raise

    async def _sequence_user_rows(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        seen: set[str] = set()
        page: list[dict[str, Any]] = []
        async for owners in self._paginate_get_collection(client, "/crm/v3/owners"):
            for owner in owners:
                user_id = owner.get("userId")
                if user_id is None:
                    continue
                user_id_str = str(user_id)
                if user_id_str in seen:
                    continue
                seen.add(user_id_str)
                page.append(
                    {
                        "user_id": user_id_str,
                        "owner_id": str(owner["id"]) if owner.get("id") is not None else None,
                        "owner_email": owner.get("email"),
                    }
                )
        if page:
            yield page

    async def _paginate_sequence_enrollments(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for contact_ids in self._paginate_crm_object_id_pages(client, "contacts"):
            page: list[dict[str, Any]] = []
            for contact_id in contact_ids:
                try:
                    data = await self._get(
                        client,
                        f"/automation/sequences/2026-03/enrollments/contact/{contact_id}",
                    )
                except httpx.HTTPStatusError as e:
                    if self._is_optional_pair_unavailable(e):
                        continue
                    raise
                page.extend(self._sequence_enrollment_rows(data, contact_id=contact_id))
            if page:
                yield page

    @staticmethod
    def _sequence_enrollment_rows(
        data: dict[str, Any],
        *,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        raw_results = data.get("results")
        raw_rows: list[Any] = raw_results if isinstance(raw_results, list) else [data]
        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_rows):
            if not isinstance(row, dict):
                continue
            sequence_id = row.get("sequenceId")
            row_id = row.get("id") or f"{contact_id}:{sequence_id or idx}"
            rows.append({**row, "id": str(row_id), "contact_id": contact_id})
        return rows

    async def _paginate_form_submissions(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for forms_page in self._paginate_get_collection(client, "/marketing/v3/forms"):
            for form in forms_page:
                form_id = form.get("id") or form.get("guid")
                if not form_id:
                    continue
                path = f"/form-integrations/v1/submissions/forms/{form_id}"
                async for submissions in self._paginate_get_collection(
                    client,
                    path,
                    limit=_FORMS_SUBMISSIONS_LIMIT,
                ):
                    page: list[dict[str, Any]] = []
                    for idx, submission in enumerate(submissions):
                        conversion_id = submission.get("conversionId")
                        submitted_at = submission.get("submittedAt")
                        row_id = conversion_id or f"{form_id}:{submitted_at}:{idx}"
                        page.append(
                            {
                                **submission,
                                "id": str(row_id),
                                "form_id": str(form_id),
                                "form_name": form.get("name"),
                            }
                        )
                    if page:
                        yield page

    async def _paginate_conversation_messages(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for threads_page in self._paginate_get_collection(
            client, "/conversations/v3/conversations/threads"
        ):
            for thread in threads_page:
                thread_id = thread.get("id")
                if not thread_id:
                    continue
                path = f"/conversations/v3/conversations/threads/{thread_id}/messages"
                async for messages in self._paginate_get_collection(client, path):
                    page = [{**message, "thread_id": str(thread_id)} for message in messages]
                    if page:
                        yield page

    async def _paginate_pipelines(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for object_type in _PIPELINE_OBJECT_TYPES:
            page = await self._pipeline_rows_for_object_type(client, object_type)
            if page:
                yield page

    async def _paginate_pipeline_stages(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for object_type in _PIPELINE_OBJECT_TYPES:
            pipelines = await self._raw_pipelines_for_object_type(client, object_type)
            page: list[dict[str, Any]] = []
            for pipeline in pipelines:
                pipeline_id = pipeline.get("id") or pipeline.get("pipelineId")
                if pipeline_id is None:
                    continue
                pipeline_external_id = f"{object_type}:{pipeline_id}"
                for stage in pipeline.get("stages") or []:
                    if not isinstance(stage, dict):
                        continue
                    stage_id = stage.get("id") or stage.get("stageId")
                    if stage_id is None:
                        continue
                    raw_metadata = stage.get("metadata")
                    metadata: dict[str, Any] = (
                        raw_metadata if isinstance(raw_metadata, dict) else {}
                    )
                    active = stage.get("active")
                    archived = stage.get("archived")
                    status = metadata.get("ticketState") or (
                        "archived" if archived is True or active is False else "active"
                    )
                    is_closed_raw = metadata.get("isClosed")
                    if isinstance(is_closed_raw, bool):
                        is_closed = is_closed_raw
                    elif is_closed_raw is None:
                        is_closed = None
                    else:
                        is_closed = str(is_closed_raw).lower() == "true"
                    page.append(
                        {
                            **stage,
                            "id": f"{object_type}:{pipeline_id}:{stage_id}",
                            "stage_id": str(stage_id),
                            "pipeline_id": pipeline_external_id,
                            "source_pipeline_id": str(pipeline_id),
                            "object_kind": object_type,
                            "name": stage.get("label"),
                            "status": status,
                            "probability": metadata.get("probability"),
                            "display_order": stage.get("displayOrder"),
                            "is_closed": is_closed,
                        }
                    )
            if page:
                yield page

    async def _pipeline_rows_for_object_type(
        self,
        client: httpx.AsyncClient,
        object_type: str,
    ) -> list[dict[str, Any]]:
        pipelines = await self._raw_pipelines_for_object_type(client, object_type)
        rows: list[dict[str, Any]] = []
        for pipeline in pipelines:
            pipeline_id = pipeline.get("id") or pipeline.get("pipelineId")
            if pipeline_id is None:
                continue
            active = pipeline.get("active")
            archived = pipeline.get("archived")
            rows.append(
                {
                    **pipeline,
                    "id": f"{object_type}:{pipeline_id}",
                    "pipeline_id": str(pipeline_id),
                    "object_kind": object_type,
                    "name": pipeline.get("label"),
                    "status": "archived" if archived is True or active is False else "active",
                }
            )
        return rows

    async def _raw_pipelines_for_object_type(
        self,
        client: httpx.AsyncClient,
        object_type: str,
    ) -> list[dict[str, Any]]:
        try:
            data = await self._get(client, f"/crm/v3/pipelines/{object_type}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {403, 404}:
                return []
            raise
        return [row for row in data.get("results", []) or [] if isinstance(row, dict)]

    @staticmethod
    def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool:
        if exc.response.status_code != 400:
            return False
        message = (HubSpotConnector._upstream_message(exc) or "").lower()
        return "paging through deleted objects is not yet supported" in message

    @staticmethod
    def _upstream_message(exc: httpx.HTTPStatusError) -> str | None:
        try:
            body = exc.response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        message = body.get("message")
        return str(message) if message else None

    async def _paginate_junction(
        self,
        client: httpx.AsyncClient,
        *,
        parent_object: str,
        target_object: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk the parent's list endpoint with `?associations=<target>`, emitting one flat row per
        (parent, target) pair. HubSpot returns associations inline on each record; no cursor — the
        v3 list endpoint isn't incremental and associations expose no modification timestamp, so
        re-walks are fine because junction upserts are idempotent."""
        parent_singular = _OBJECT_SINGULARS[parent_object]
        target_singular = _OBJECT_SINGULARS[target_object]
        parent_field = f"{parent_singular}_id"
        target_field = f"{target_singular}_id"
        after: str | None = None
        while True:
            qs = f"limit={PAGE_LIMIT}&properties=hs_object_id&associations={target_object}"
            if after:
                qs += f"&after={after}"
            data = await self._get(client, f"/crm/v3/objects/{parent_object}?{qs}")
            rows: list[dict[str, Any]] = []
            for parent in data.get("results", []) or []:
                if not isinstance(parent, dict):
                    continue
                parent_id = parent.get("id")
                if not isinstance(parent_id, str):
                    continue
                assoc_block = (parent.get("associations") or {}).get(target_object) or {}
                for assoc in assoc_block.get("results") or []:
                    if not isinstance(assoc, dict):
                        continue
                    target_id = assoc.get("id")
                    if not isinstance(target_id, str):
                        continue
                    rows.append(
                        {
                            "id": f"{parent_id}:{target_id}",
                            parent_field: parent_id,
                            target_field: target_id,
                            "type": assoc.get("type"),
                        }
                    )
            if rows:
                yield rows
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return
