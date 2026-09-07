"""The Freshdesk REST v2 connector — the helpdesk surface (tickets, conversations, contacts,
companies, agents), the admin/config objects, the solutions knowledge-base tree, and the forums
tree synced as recallable pages.

Records arrive flat — no envelope to lift. `paginate` has three shapes. `tickets` uses page-number
incrementing (`?page=N&per_page=100`) with `?updated_since=<iso>&order_by=updated_at` for
incremental, bounded at Freshdesk's 300-page ceiling. The nested trees fan out from a parent
(conversations per ticket; the two- and three-level solutions/forums walks). Everything else follows
RFC 5988 `Link: rel=next` cursor pagination. Auth is HTTP Basic with the API key as the username and
any non-empty password (the documented `"X"`): when the resolved `Credential` carries a direct key,
`_make_client` sends it as Basic auth; a broker's proxying transport is honored unchanged. The base
URL is per-tenant (`https://<domain>.freshdesk.com`), so the class default is empty and a run
without a resolved host fails loud. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_LIMIT = 100
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
TICKET_PAGE_CEILING = 300
SETTINGS_PAGE_KEY = "helpdesk"
_REFUSAL_STATUS = frozenset({401, 403})

# Stream-name → REST resource path. Substreams / nested trees are dispatched separately.
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
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


FRESHDESK_STREAMS: list[StreamSpec] = [
    _stream("tickets", cursor_field="updated_at", canonical=True),
    _stream("conversations", canonical=True),
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
    _stream("canned_responses"),
    _stream("solution_categories", source_object="solutions/categories"),
    _stream("solution_folders"),
    _stream("solution_articles", canonical=True),
    _stream("discussion_categories", source_object="discussions/categories"),
    _stream("discussion_forums"),
    _stream("discussion_topics"),
    _stream("discussion_comments"),
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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            special = self._special_pages(client, stream.name, cursor)
            if special is not None:
                async for page in special:
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

    def _special_pages(
        self, client: httpx.AsyncClient, name: str, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]] | None:
        if name == "tickets":
            return self._paginate_tickets(client, cursor=cursor)
        if name == "conversations":
            return self._paginate_conversations(client, cursor=cursor)
        two_level = {
            "canned_responses": (
                "/api/v2/canned_response_folders",
                "/api/v2/canned_response_folders/{id}/responses",
            ),
            "solution_folders": (
                "/api/v2/solutions/categories",
                "/api/v2/solutions/categories/{id}/folders",
            ),
            "discussion_forums": (
                "/api/v2/discussions/categories",
                "/api/v2/discussions/categories/{id}/forums",
            ),
            "discussion_topics": (
                "/api/v2/discussions/forums",
                "/api/v2/discussions/forums/{id}/topics",
            ),
            "discussion_comments": (
                "/api/v2/discussions/topics",
                "/api/v2/discussions/topics/{id}/comments",
            ),
        }.get(name)
        if two_level is not None:
            parent_path, child_path = two_level
            return self._paginate_two_level(
                client, parent_path=parent_path, child_path_template=child_path
            )
        if name == "solution_articles":
            return self._paginate_three_level(
                client,
                root_path="/api/v2/solutions/categories",
                mid_path_template="/api/v2/solutions/categories/{id}/folders",
                leaf_path_template="/api/v2/solutions/folders/{id}/articles",
            )
        return None

    async def _paginate_link_header(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """RFC 5988 link-header walk starting at `?per_page=100`."""
        async for page in self._get_link_header_pages(
            client, path, params=params, page_size=PAGE_LIMIT
        ):
            yield page

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

    async def _paginate_conversations(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk every ticket the cursor admits, then fetch its conversations, stamping `ticket_id`
        (Freshdesk returns it, but a defensive stamp guards a future API change)."""
        async for ticket_page in self._paginate_tickets(client, cursor=cursor):
            for ticket in ticket_page:
                tid = ticket.get("id") if isinstance(ticket, dict) else None
                if tid is None:
                    continue
                async for convo_page in self._paginate_link_header(
                    client, f"/api/v2/tickets/{tid}/conversations"
                ):
                    for convo in convo_page:
                        if isinstance(convo, dict):
                            convo.setdefault("ticket_id", tid)
                    yield convo_page

    async def _paginate_two_level(
        self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for parent_page in self._paginate_link_header(client, parent_path):
            for parent in parent_page:
                pid = parent.get("id") if isinstance(parent, dict) else None
                if pid is None:
                    continue
                async for page in self._paginate_link_header(
                    client, child_path_template.format(id=pid)
                ):
                    yield page

    async def _paginate_three_level(
        self,
        client: httpx.AsyncClient,
        *,
        root_path: str,
        mid_path_template: str,
        leaf_path_template: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for cat_page in self._paginate_link_header(client, root_path):
            for cat in cat_page:
                cid = cat.get("id") if isinstance(cat, dict) else None
                if cid is None:
                    continue
                async for folder_page in self._paginate_link_header(
                    client, mid_path_template.format(id=cid)
                ):
                    for folder in folder_page:
                        fid = folder.get("id") if isinstance(folder, dict) else None
                        if fid is None:
                            continue
                        async for leaf_page in self._paginate_link_header(
                            client, leaf_path_template.format(id=fid)
                        ):
                            yield leaf_page
