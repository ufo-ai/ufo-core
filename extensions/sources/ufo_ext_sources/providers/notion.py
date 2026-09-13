"""The Notion connector — pages, their block bodies, databases, comments, and users as recallable
prose.

Notion is a document store, so this is the connector that overrides `render`: a page's or block's
content lives in `rich_text` runs and type-keyed block payloads, not in flat columns, so the default
JSON dump would be unrecallable. `render` lifts the readable text — a page's title and property
values, a block's paragraph/heading/list/to-do/code text, a comment's body, a user's name and email
— so a synced page recalls as what a member would read.

Reads route through three shapes. `pages` and `data_sources` come from `POST /search` filtered by
object type, sorted ascending by `last_edited_time`, paged by the `next_cursor`/`has_more` envelope
and watermarked on `last_edited_time`. `blocks` hangs under `pages` and under itself at the same
`GET /blocks/{id}/children`.
Only the self-edge narrows: a block holds further blocks where `has_children` is true and its type
is not one that carries another object's body under an id of its own. A page answers neither field,
which is why the edge under `pages` names no predicate at all. `comments` fans out
`GET /comments?block_id={id}` under `pages`. `users` is a flat `GET /users`.
Every collection GET sends the `Notion-Version` header and paginates on
`start_cursor`/`next_cursor`. A grant whose integration lacks the capability for a
stream (`401`/`403`) yields `StreamSkipped` so the run records a skip, not a failure. The credential
is resolved through the auth proxy the runner threads — this connector holds no token. The write
path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
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
    list_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
NOTION_VERSION = "2025-09-03"
BLOCK_CHILDREN_PATH = "/blocks/{id}/children"
_REFUSAL_STATUS = frozenset({401, 403})

NOTION_STREAMS: list[StreamSpec] = [
    StreamSpec(name="users", source_object="users", primary_key="id"),
    StreamSpec(
        name="pages",
        source_object="page",
        primary_key="id",
        cursor_field="last_edited_time",
        created_at_field="created_time",
        updated_at_field="last_edited_time",
        canonical=True,
    ),
    StreamSpec(
        name="data_sources",
        source_object="data_source",
        primary_key="id",
        cursor_field="last_edited_time",
        created_at_field="created_time",
        updated_at_field="last_edited_time",
        canonical=True,
    ),
    StreamSpec(
        name="comments",
        source_object="comments",
        primary_key="id",
        cursor_field="created_time",
        created_at_field="created_time",
        updated_at_field=None,
        ordering=Ordering.ascending,
        parents=(ParentEdge(stream="pages", path="/comments?block_id={id}"),),
    ),
    StreamSpec(
        name="blocks",
        source_object="blocks",
        primary_key="id",
        cursor_field="last_edited_time",
        created_at_field="created_time",
        updated_at_field="last_edited_time",
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(stream="pages", path=BLOCK_CHILDREN_PATH),
            ParentEdge(
                stream="blocks",
                path=BLOCK_CHILDREN_PATH,
                where={"has_children": (True,)},
                unless={"type": ("child_page", "child_database", "ai_block")},
            ),
        ),
    ),
]


class NotionConnector(RestConnector):
    name = "notion"
    base_url = "https://api.notion.com/v1"
    streams_list = NOTION_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Notion-Version"] = NOTION_VERSION
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            match stream.name:
                case "users":
                    async for page in self._collection(client, "/users"):
                        yield page
                case "pages":
                    async for page in self._search(client, object_type="page", cursor=run.cursor):
                        yield page
                case "data_sources":
                    async for page in self._search(
                        client, object_type="data_source", cursor=run.cursor
                    ):
                        yield page
                case _ if stream.parents:
                    pages = partial(self._partition_pages, client, stream)
                    async for stream_page in fanned_out(stream, run, pages):
                        yield stream_page
                case _:
                    raise StreamSkipped(f"notion stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"notion: {stream.name!r} refused ({error.response.status_code}); the "
                    "integration lacks the capability or was not shared the content"
                ) from error
            raise

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One parent's collection, resumed past that partition's own watermark. Notion publishes no
        server-side `since` on either child endpoint, so the bound is applied to the records."""
        target = httpx.URL(partition.path)
        field = stream.cursor_field or ""
        async for records in self._collection(client, target.path, params=dict(target.params)):
            kept = [
                record
                for record in records
                if not bound.after or str(record.get(field) or "") > bound.after
            ]
            if not kept:
                continue
            values = [str(record.get(field) or "") for record in kept]
            yield WalkPage(records=kept, high=max(values), low=min(values))

    async def _search(
        self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """POST `/search` paged by the `next_cursor`/`has_more` envelope. Notion has no server-side
        `since`, so an incremental run filters the ascending-sorted results past the stored
        `last_edited_time` watermark client-side."""
        next_cursor: str | None = None
        while True:
            body: dict[str, Any] = {
                "page_size": PAGE_SIZE,
                "sort": {"timestamp": "last_edited_time", "direction": "ascending"},
                "filter": {"property": "object", "value": object_type},
            }
            if next_cursor:
                body["start_cursor"] = next_cursor
            data = await self._post(client, "/search", json=body)
            records = list_or_empty(data.get("results"))
            if cursor:
                records = [r for r in records if str(r.get("last_edited_time") or "") > cursor]
            if records:
                yield records
            next_cursor = data.get("next_cursor")
            if not isinstance(next_cursor, str) or not data.get("has_more"):
                return

    async def _collection(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """A GET-paginated Notion collection: `results` records, `start_cursor`→`next_cursor`."""
        async for page in self._get_cursor_pages(
            client,
            path,
            records_path="results",
            next_cursor_path="next_cursor",
            params=params,
            cursor_param="start_cursor",
            page_size_param="page_size",
            page_size=PAGE_SIZE,
        ):
            yield page

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "pages":
                title = _page_title(record) or ""
                body = _properties_text(record)
            case "data_sources":
                title = _rich_text_text(record.get("title")) or _str(record.get("name"))
                body = _rich_text_text(record.get("description"))
            case "blocks":
                title = ""
                body = _block_text(record)
            case "comments":
                title = ""
                body = _rich_text_text(record.get("rich_text"))
            case "users":
                title = _str(record.get("name"))
                body = _user_text(record)
            case _:
                return super().render(record, stream)
        if not title:
            title = body.splitlines()[0] if body else super().render(record, stream)[0]
        heading = f"# notion {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _rich_text_text(value: Any) -> str:
    """Concatenate the `plain_text` of a Notion rich-text run list into readable text."""
    if not isinstance(value, list):
        return ""
    return "".join(
        part["plain_text"]
        for part in value
        if isinstance(part, dict) and isinstance(part.get("plain_text"), str)
    ).strip()


def _page_title(page: dict[str, Any]) -> str:
    props = page.get("properties")
    if not isinstance(props, dict):
        return ""
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = _rich_text_text(prop.get("title"))
            if title:
                return title
    return ""


def _properties_text(page: dict[str, Any]) -> str:
    props = page.get("properties")
    if not isinstance(props, dict):
        return ""
    lines = [
        f"{name}: {text}"
        for name, prop in props.items()
        if isinstance(prop, dict) and (text := _property_text(prop))
    ]
    return "\n".join(lines)


def _property_text(prop: dict[str, Any]) -> str:
    ptype = prop.get("type")
    value = prop.get(ptype) if isinstance(ptype, str) else None
    match ptype:
        case "title" | "rich_text":
            return _rich_text_text(value)
        case "select" | "status":
            return _str(value.get("name")) if isinstance(value, dict) else ""
        case "multi_select" | "people":
            return (
                ", ".join(_str(item.get("name")) for item in value if isinstance(item, dict))
                if isinstance(value, list)
                else ""
            )
        case "date":
            return _str(value.get("start")) if isinstance(value, dict) else ""
        case "number" | "url" | "email" | "phone_number" | "checkbox":
            return str(value) if value is not None else ""
        case _:
            return ""


def _block_text(block: dict[str, Any]) -> str:
    btype = block.get("type")
    content = block.get(btype) if isinstance(btype, str) else None
    if not isinstance(content, dict):
        return ""
    if btype in {"child_page", "child_database"}:
        return _str(content.get("title"))
    text = _rich_text_text(content.get("rich_text"))
    if btype == "to_do":
        return f"{'[x]' if content.get('checked') else '[ ]'} {text}".strip()
    return text


def _user_text(record: dict[str, Any]) -> str:
    person = record.get("person")
    email = person.get("email") if isinstance(person, dict) else None
    return "\n".join(part for part in (_str(record.get("name")), _str(email)) if part)
