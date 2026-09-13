"""The Typeform connector — forms, responses, workspaces, themes, and webhooks synced as recallable
pages.

Typeform paginates collections by `?page=N&page_size=200` over an `{items: [...], page_count}`
envelope, looping until the reported page count is reached. A refusal (401/403) raises
`StreamSkipped`; an unimplemented stream raises it too. The credential is resolved through the auth
proxy the runner threads; this connector holds no token. The write path is intentionally absent —
the source seam only reads.

`responses` and `webhooks` are published only under a form, so both hang under `forms`. Responses
page through the shared body-cursor pager (`next_page_token` fed back as `after`) and climb
`submitted_at`, which each form's endpoint bounds server-side as `?since`. A response `token` is
unique across the account, so `responses` declares `key_scope="global"`."""

from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    Ordering,
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
    records_at,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 200
RESPONSES_PAGE_SIZE = 1000
_REFUSAL_STATUS = frozenset({401, 403})


def _cursor_bounds(records: list[dict[str, Any]], field: str) -> tuple[str | None, str | None]:
    values = sorted(str(record[field]) for record in records if isinstance(record.get(field), str))
    return (values[-1], values[0]) if values else (None, None)


TYPEFORM_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="forms",
        source_object="forms",
        primary_key="id",
        cursor_field="last_updated_at",
        updated_at_field="last_updated_at",
        canonical=True,
    ),
    StreamSpec(
        name="responses",
        source_object="responses",
        primary_key="token",
        cursor_field="submitted_at",
        created_at_field="submitted_at",
        updated_at_field=None,
        canonical=True,
        ordering=Ordering.ascending,
        parents=(ParentEdge(stream="forms", path="/forms/{id}/responses"),),
        key_scope="global",
    ),
    StreamSpec(name="workspaces", source_object="workspaces", primary_key="id"),
    StreamSpec(name="images", source_object="images", primary_key="id"),
    StreamSpec(name="themes", source_object="themes", primary_key="id"),
    StreamSpec(
        name="webhooks",
        source_object="webhooks",
        primary_key="id",
        parents=(ParentEdge(stream="forms", path="/forms/{id}/webhooks"),),
    ),
]


class TypeformConnector(RestConnector):
    name = "typeform"
    base_url = "https://api.typeform.com"
    streams_list = TYPEFORM_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != "webhooks":
            return super().record_ref(record, stream)
        value = record.get("tag")
        return str(value) if isinstance(value, (str, int)) else None

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "forms":
                async for page in self._forms(client, cursor=run.cursor):
                    yield page
                return
            if stream.name == "responses":
                pages = partial(self._response_pages, client)
                async for answers in fanned_out(stream, run, pages):
                    yield answers
                return
            if stream.name in {"workspaces", "images", "themes"}:
                async for page in self._paged_items(client, f"/{stream.source_object}"):
                    yield page
                return
            if stream.name == "webhooks":
                pages = partial(self._webhook_pages, client)
                async for hooks in fanned_out(stream, run, pages):
                    yield hooks
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

    async def _response_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        params = {"since": bound.after} if bound.after else {}
        async for items in self._get_cursor_pages(
            client,
            partition.path,
            records_path="items",
            next_cursor_path="next_page_token",
            params=params,
            cursor_param="after",
            page_size_param="page_size",
            page_size=RESPONSES_PAGE_SIZE,
        ):
            high, low = _cursor_bounds(items, "submitted_at")
            yield WalkPage(records=items, high=high, low=low)

    async def _webhook_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        data = await self._get(client, partition.path)
        records = records_at(data, "items")
        if records:
            yield WalkPage(records=records)
