"""The Typeform connector — forms, responses, workspaces, themes, and webhooks synced as recallable
pages.

Typeform paginates collections by `?page=N&page_size=200` over an `{items: [...], page_count}`
envelope, looping until the reported page count is reached. `responses` fans out per form: it
walks the forms first, then pulls each form's responses through the shared body-cursor pager
(`next_page_token` fed back as `after`), threading `?since` for incremental runs and stamping the
`form_id`/`form_title` context onto each response. `webhooks` fans out per form as a single GET. A
refusal (401/403) raises `StreamSkipped`; an unimplemented stream raises it too. The credential is
resolved through the auth proxy the runner threads; this connector holds no token. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, records_at, with_context

PAGE_SIZE = 200
RESPONSES_PAGE_SIZE = 1000
_REFUSAL_STATUS = frozenset({401, 403})

TYPEFORM_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="forms",
        source_object="forms",
        primary_key="id",
        cursor_field="last_updated_at",
        updated_at_field="last_updated_at",
    ),
    StreamSpec(
        name="responses",
        source_object="responses",
        primary_key="token",
        cursor_field="submitted_at",
        created_at_field="submitted_at",
        updated_at_field=None,
    ),
    StreamSpec(name="workspaces", source_object="workspaces", primary_key="id"),
    StreamSpec(name="images", source_object="images", primary_key="id", canonical=False),
    StreamSpec(name="themes", source_object="themes", primary_key="id", canonical=False),
    StreamSpec(name="webhooks", source_object="webhooks", primary_key="tag", canonical=False),
]


class TypeformConnector(RestConnector):
    name = "typeform"
    base_url = "https://api.typeform.com"
    streams_list = TYPEFORM_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "forms":
                async for page in self._forms(client, cursor=cursor):
                    yield page
                return
            if stream.name == "responses":
                async for page in self._responses(client, cursor=cursor):
                    yield page
                return
            if stream.name in {"workspaces", "images", "themes"}:
                async for page in self._paged_items(client, f"/{stream.source_object}"):
                    yield page
                return
            if stream.name == "webhooks":
                async for page in self._webhooks(client):
                    yield page
                return
            raise StreamSkipped(f"typeform stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"typeform: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _paged_items(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1
        while True:
            query = dict(params or {})
            query["page"] = page
            query.setdefault("page_size", PAGE_SIZE)
            data = await self._get(client, path, params=query)
            records = records_at(data, "items")
            if records:
                yield records
            page_count = data.get("page_count")
            if isinstance(page_count, int):
                if page >= page_count:
                    return
            elif len(records) < PAGE_SIZE:
                return
            page += 1

    async def _forms(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for forms in self._paged_items(client, "/forms"):
            if cursor:
                forms = [f for f in forms if str(f.get("last_updated_at") or "") > cursor]
            if forms:
                yield forms

    async def _responses(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for forms in self._forms(client, cursor=None):
            for form in forms:
                form_id = form.get("id")
                if not isinstance(form_id, str) or not form_id:
                    continue
                params: dict[str, Any] = {}
                if cursor:
                    params["since"] = cursor
                async for items in self._get_cursor_pages(
                    client,
                    f"/forms/{form_id}/responses",
                    records_path="items",
                    next_cursor_path="next_page_token",
                    params=params,
                    cursor_param="after",
                    page_size_param="page_size",
                    page_size=RESPONSES_PAGE_SIZE,
                ):
                    if items:
                        yield with_context(items, form_id=form_id, form_title=form.get("title"))

    async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        async for forms in self._forms(client, cursor=None):
            for form in forms:
                form_id = form.get("id")
                if not isinstance(form_id, str) or not form_id:
                    continue
                data = await self._get(client, f"/forms/{form_id}/webhooks")
                records = records_at(data, "items")
                if records:
                    yield with_context(records, form_id=form_id, form_title=form.get("title"))
