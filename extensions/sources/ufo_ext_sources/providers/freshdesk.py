"""The Freshdesk REST v2 connector — the helpdesk surface (tickets, conversations, contacts,
companies, agents), the admin/config objects, the solutions knowledge-base tree, and the forums
tree synced as recallable pages.

Records arrive flat — no envelope to lift. `tickets` uses page-number incrementing
(`?page=N&per_page=100`) with `?updated_since=<iso>&order_by=updated_at` for incremental, bounded at
Freshdesk's 300-page ceiling. Everything else follows
RFC 5988 `Link: rel=next` cursor pagination. Auth is HTTP Basic with the API key as the username and
any non-empty password (the documented `"X"`): when the resolved `Credential` carries a direct key,
`_make_client` sends it as Basic auth; a broker's proxying transport is honored unchanged. The base
URL is per-tenant (`https://<domain>.freshdesk.com`), so the class default is empty and a run
without a resolved host fails loud. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads.

Freshdesk publishes no flat `/conversations`, `/discussions/forums`, `/discussions/topics` or
`/solutions/articles`, so every collection below the root declares the parent it hangs under. A
Freshdesk id is unique across the tenant, so `conversations` and `solution_articles` declare
`key_scope="global"`."""

from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any, Literal

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_LIMIT = 100
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
TICKET_PAGE_CEILING = 300
SETTINGS_PAGE_KEY = "helpdesk"
CONVERSATIONS_FETCH_BUDGET = 20
SOLUTIONS_FETCH_BUDGET = 5
_REFUSAL_STATUS = frozenset({401, 403})

_SIMPLE_PATHS: dict[str, str] = {
    "agents": "/api/v2/agents",
    "business_hours": "/api/v2/business_hours",
    "canned_response_folders": "/api/v2/canned_response_folders",
    "companies": "/api/v2/companies",
    "contacts": "/api/v2/contacts",
    "discussion_categories": "/api/v2/discussions/categories",
    "email_configs": "/api/v2/email_configs",
    "email_mailboxes": "/api/v2/email/mailboxes",
    "groups": "/api/v2/groups",
    "products": "/api/v2/products",
    "roles": "/api/v2/roles",
    "satisfaction_ratings": "/api/v2/surveys/satisfaction_ratings",
    "scenario_automations": "/api/v2/scenario_automations",
    "settings": "/api/v2/settings/helpdesk",
    "skills": "/api/v2/skills",
    "sla_policies": "/api/v2/sla_policies",
    "solution_categories": "/api/v2/solutions/categories",
    "surveys": "/api/v2/surveys",
    "ticket_fields": "/api/v2/ticket_fields",
    "time_entries": "/api/v2/time_entries",
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    canonical: bool = False,
    indexed: bool = True,
    parent: str | None = None,
    path: str | None = None,
    refan: Literal["on_parent_change"] | None = None,
    key_scope: Literal["local", "global"] = "local",
    fetch_budget: int | None = None,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
        indexed=indexed,
        parents=()
        if parent is None or path is None
        else (ParentEdge(stream=parent, path=path, refan=refan),),
        key_scope=key_scope,
        fetch_budget=fetch_budget,
    )


FRESHDESK_STREAMS: list[StreamSpec] = [
    _stream("tickets", cursor_field="updated_at", canonical=True),
    _stream(
        "conversations",
        canonical=True,
        parent="tickets",
        path="/api/v2/tickets/{id}/conversations",
        refan="on_parent_change",
        key_scope="global",
        fetch_budget=CONVERSATIONS_FETCH_BUDGET,
    ),
    _stream("contacts", cursor_field="updated_at", canonical=True),
    _stream("companies", canonical=True),
    _stream("agents"),
    _stream("groups"),
    _stream("roles"),
    _stream("skills"),
    _stream("business_hours"),
    _stream("sla_policies"),
    _stream("scenario_automations"),
    _stream("settings", source_object="settings/helpdesk"),
    _stream("ticket_fields"),
    _stream("products"),
    _stream("email_configs"),
    _stream("email_mailboxes", source_object="email/mailboxes"),
    _stream("canned_response_folders"),
    _stream(
        "canned_responses",
        parent="canned_response_folders",
        path="/api/v2/canned_response_folders/{id}/responses",
    ),
    _stream("solution_categories", source_object="solutions/categories", indexed=False),
    _stream(
        "solution_folders",
        indexed=False,
        parent="solution_categories",
        path="/api/v2/solutions/categories/{id}/folders",
        fetch_budget=SOLUTIONS_FETCH_BUDGET,
    ),
    _stream(
        "solution_articles",
        canonical=True,
        parent="solution_folders",
        path="/api/v2/solutions/folders/{id}/articles",
        key_scope="global",
        fetch_budget=SOLUTIONS_FETCH_BUDGET,
    ),
    _stream("discussion_categories", source_object="discussions/categories"),
    _stream(
        "discussion_forums",
        parent="discussion_categories",
        path="/api/v2/discussions/categories/{id}/forums",
    ),
    _stream(
        "discussion_topics",
        parent="discussion_forums",
        path="/api/v2/discussions/forums/{id}/topics",
    ),
    _stream(
        "discussion_comments",
        parent="discussion_topics",
        path="/api/v2/discussions/topics/{id}/comments",
    ),
    _stream("satisfaction_ratings", source_object="surveys/satisfaction_ratings"),
    _stream("surveys"),
    _stream("time_entries"),
]


class FreshdeskConnector(RestConnector):
    name = "freshdesk"
    base_url = ""
    streams_list = FRESHDESK_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name == "settings":
            return SETTINGS_PAGE_KEY
        return super().record_identity(record, stream)

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != "settings":
            return super().record_ref(record, stream)
        value = record.get("primary_language")
        return str(value) if isinstance(value, (str, int)) else None

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        timeout = httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS)
        base = base_url.rstrip("/")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential.transport is not None:
            return httpx.AsyncClient(
                base_url=base, transport=credential.transport, timeout=timeout, headers=headers
            )
        if credential.bearer is not None:
            return httpx.AsyncClient(
                base_url=base,
                timeout=timeout,
                auth=httpx.BasicAuth(credential.bearer, "X"),
                headers=headers,
            )
        raise RuntimeError("freshdesk: credential carries no auth")

    @staticmethod
    def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "per_page": PAGE_LIMIT,
            "page": page,
            "order_by": "updated_at",
            "order_type": "asc",
            "include": "description,requester,stats",
        }
        if cursor:
            params["updated_since"] = cursor
        return params

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.parents:
                pages = partial(self._child_pages, client)
                async for under in fanned_out(stream, run, pages):
                    yield under
                return
            if stream.name == "tickets":
                async for page in self._paginate_tickets(client, cursor=run.cursor):
                    yield page
                return
            path = _SIMPLE_PATHS.get(stream.name)
            if not path:
                raise NotImplementedError(
                    f"freshdesk: stream {stream.name!r} has no paginate dispatch"
                )
            if stream.name == "settings":
                data = await self._get(client, path)
                if isinstance(data, dict):
                    yield [data]
                return
            async for page in self._paginate_link_header(client, path):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"freshdesk: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _paginate_link_header(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """RFC 5988 link-header walk starting at `?per_page=100`."""
        async for page in self._get_link_header_pages(
            client, path, params=params, page_size=PAGE_LIMIT
        ):
            yield page

    async def _child_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        async for page in self._paginate_link_header(client, partition.path):
            yield WalkPage(records=page)

    async def _paginate_tickets(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Page-number pagination with `updated_since=<cursor>`, stopping on a short page or at
        Freshdesk's 300-page ceiling (beyond which the API 400s — the caller slices the window)."""
        page = 1
        while True:
            params = self._build_tickets_params(cursor, page)
            data = await self._get(client, "/api/v2/tickets", params=params)
            records = data if isinstance(data, list) else (data.get("results") or [])
            if not records:
                return
            yield records
            if len(records) < PAGE_LIMIT:
                return
            page += 1
            if page > TICKET_PAGE_CEILING:
                return
