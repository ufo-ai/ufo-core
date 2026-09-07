"""The Confluence connector — spaces, wiki pages, blog posts, comments, groups, and the audit log
synced as recallable prose.

Confluence stores a page's body as storage-format XHTML (headings, lists, tables, and namespaced
`ac:*` macro tags), not flat text, so this is a connector that overrides `render`: the default JSON
dump of a storage-format string is unrecallable. `render` feeds that XHTML through an HTML text
extractor that keeps the character data, drops every tag and attribute, unescapes entities, and
breaks lines on block boundaries, so a synced page recalls as what a member would read.

Reads route through Atlassian's OAuth gateway. A grant reaches one or more sites, so every stream
first enumerates `/oauth/token/accessible-resources` and fans out over each site's `cloud_id`,
hitting `/ex/confluence/{cloud_id}/...`. The collections page by `start`+`limit` and continue while
the response carries a `_links.next` HATEOAS link. `pages`, `blog_posts`, `comments`, and `audit`
are incremental — each record's cursor field is filtered past the stored watermark (Confluence has
no server-side `since`), so a re-run reads all pages but lands only records newer than the cursor. A
record id is site-scoped (`{cloud_id}:{id}`) so two sites never collide on one ref. A grant that
lacks the scope or was not shared the space (`401`/`403`) yields `StreamSkipped` so the run records
a skip, not a failure. The credential is resolved through the auth proxy the runner threads — this
connector holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from html.parser import HTMLParser
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    get_path,
    list_or_empty,
    records_at,
    with_context,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 50
_REFUSAL_STATUS = frozenset({401, 403})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "br",
        "div",
        "hr",
        "li",
        "tr",
        "td",
        "th",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

_PATHS: dict[str, str] = {
    "spaces": "wiki/api/v2/spaces",
    "pages": "wiki/api/v2/pages",
    "blog_posts": "wiki/api/v2/blogposts",
    "comments": "wiki/api/v2/footer-comments",
    "groups": "wiki/rest/api/group",
    "audit": "wiki/rest/api/audit",
}
_PARAMS: dict[str, dict[str, Any]] = {
    "spaces": {"expand": "permissions,icon,description.plain,description.view"},
    "pages": {"body-format": "storage"},
    "blog_posts": {"body-format": "storage"},
    "comments": {"body-format": "storage"},
}

CONFLUENCE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="spaces", source_object="spaces", primary_key="id", canonical=True),
    StreamSpec(
        name="pages",
        source_object="pages",
        primary_key="id",
        cursor_field="version.createdAt",
        created_at_field="createdAt",
        updated_at_field="version.createdAt",
        canonical=True,
    ),
    StreamSpec(
        name="blog_posts",
        source_object="blogposts",
        primary_key="id",
        cursor_field="version.createdAt",
        created_at_field="createdAt",
        updated_at_field="version.createdAt",
        canonical=True,
    ),
    StreamSpec(
        name="comments",
        source_object="footer-comments",
        primary_key="id",
        cursor_field="version.createdAt",
        created_at_field="createdAt",
        updated_at_field="version.createdAt",
        canonical=True,
    ),
    StreamSpec(name="groups", source_object="group", primary_key="id"),
    StreamSpec(
        name="audit",
        source_object="audit",
        primary_key="creationDate",
        cursor_field="creationDate",
        created_at_field="creationDate",
        updated_at_field=None,
    ),
]


def _body_text(record: Mapping[str, Any]) -> str | None:
    value = get_path(record, "body.storage.value") or get_path(record, "body.view.value")
    return value if isinstance(value, str) and value else None


class ConfluenceConnector(RestConnector):
    name = "confluence"
    base_url = "https://api.atlassian.com"
    streams_list = CONFLUENCE_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        suffix = _PATHS.get(stream.name)
        if suffix is None:
            raise StreamSkipped(f"confluence stream {stream.name!r} is not implemented")
        try:
            for site in await self._sites(client):
                cloud_id = site.get("id")
                if not isinstance(cloud_id, str) or not cloud_id:
                    continue
                path = f"/ex/confluence/{cloud_id}/{suffix}"
                async for page in self._offset_results(
                    client,
                    path,
                    params=_PARAMS.get(stream.name),
                    cursor=cursor,
                    cursor_field=stream.cursor_field,
                ):
                    yield with_context(page, cloud_id=cloud_id, site_url=site.get("url"))
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"confluence: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks the scope or was not shared the space"
                ) from error
            raise

    async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """The sites a grant reaches, each an `{id, url, ...}` whose `id` is the `cloud_id` every
        Confluence path is scoped under."""
        response = await self._get_raw(client, "/oauth/token/accessible-resources")
        return list_or_empty(response.json() if response.content else [])

    async def _offset_results(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        cursor_field: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk a collection by `start`+`limit`, continuing while the response carries a
        `_links.next` HATEOAS link. An incremental stream filters each page past the stored
        watermark on `cursor_field` (Confluence has no server-side `since`), so a re-run reads all
        pages but lands only records newer than the cursor."""
        start = 0
        while True:
            query = {"start": start, "limit": PAGE_SIZE, **(params or {})}
            data = await self._get(client, path, params=query)
            records = records_at(data, "results")
            if cursor and cursor_field:
                records = [r for r in records if str(get_path(r, cursor_field, "") or "") > cursor]
            if records:
                yield records
            if not records or not get_path(data, "_links.next"):
                return
            start += len(records)

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Shape each stream onto its canonical recall fields (name/title/kind/url/body/author/
        created_at/parent_external_id), then site-scope the record id (`{cloud_id}:{id}`) so two
        sites never collide on one ref, and lift a nested `version.createdAt` cursor to the flat key
        the sync advances a watermark over. A stream whose id IS its cursor (`audit` keys on
        `creationDate`) keeps the raw id — scoping it would corrupt the watermark comparison."""
        webui = get_path(record, "_links.webui")
        url = f"{record.get('site_url')}{webui}" if isinstance(webui, str) else None
        if stream.name == "spaces":
            flat = {
                **record,
                "name": record.get("name"),
                "api_url": record.get("_links", {}).get("self")
                if isinstance(record.get("_links"), dict)
                else None,
                "created_at": record.get("createdAt"),
            }
        elif stream.name in {"pages", "blog_posts"}:
            flat = {
                **record,
                "title": record.get("title"),
                "kind": "blog_post" if stream.name == "blog_posts" else "page",
                "url": url,
                "body": _body_text(record),
                "created_at": record.get("createdAt"),
                "updated_at": get_path(record, "version.createdAt"),
            }
        elif stream.name == "comments":
            flat = {
                **record,
                "body": _body_text(record),
                "author": get_path(record, "version.authorId"),
                "url": url,
                "created_at": record.get("createdAt"),
                "parent_external_id": record.get("pageId") or record.get("blogPostId"),
            }
        else:
            flat = dict(record)
        cloud_id = record.get("cloud_id")
        external_id = record.get(stream.primary_key)
        if (
            isinstance(cloud_id, str)
            and external_id is not None
            and stream.primary_key != stream.cursor_field
        ):
            flat[stream.primary_key] = f"{cloud_id}:{external_id}"
        if stream.cursor_field and "." in stream.cursor_field:
            flat[stream.cursor_field] = get_path(record, stream.cursor_field)
        return flat

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "pages" | "blog_posts" | "comments":
                title = _str(record.get("title"))
                body = _StorageTextExtractor.extract(record.get("body"))
            case "spaces":
                title = _str(record.get("name")) or _str(record.get("key"))
                body = _StorageTextExtractor.extract(
                    get_path(record, "description.view.value")
                    or get_path(record, "description.plain.value")
                )
            case _:
                return super().render(record, stream)
        if not title:
            title = body.splitlines()[0] if body else super().render(record, stream)[0]
        heading = f"# confluence {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


class _StorageTextExtractor(HTMLParser):
    """Lift readable text from Confluence storage-format XHTML: keep the character data, break
    lines on block-level boundaries, and drop every tag and attribute. Entities are unescaped by
    the parser (`convert_charrefs`); namespaced macro tags (`ac:*`, `ri:*`) carry no readable text
    of their own, so they fall through as transparent containers and only inner blocks break."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    @classmethod
    def extract(cls, raw: Any) -> str:
        if not isinstance(raw, str) or not raw:
            return ""
        parser = cls()
        parser.feed(raw)
        return parser._text()

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def _text(self) -> str:
        joined = "".join(self._parts)
        lines = (" ".join(line.split()) for line in joined.split("\n"))
        return "\n".join(line for line in lines if line).strip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
