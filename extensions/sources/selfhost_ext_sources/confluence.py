"""The Confluence connector — spaces, wiki pages, blog posts, and comments synced as recallable
prose.

Confluence stores a page's body as storage-format XHTML (headings, lists, tables, and namespaced
`ac:*` macro tags), not flat text, so this is a connector that overrides `render`: the default JSON
dump of a storage-format string is unrecallable. `render` feeds that XHTML through an HTML text
extractor that keeps the character data, drops every tag and attribute, unescapes entities, and
breaks lines on block boundaries, so a synced page recalls as what a member would read — a space
by its name and description, a page or blog post by its title and body, a comment by its text.

Reads route through Atlassian's OAuth gateway. A grant reaches one or more sites, so every stream
first enumerates `/oauth/token/accessible-resources` and fans out over each site's `cloud_id`,
hitting `/ex/confluence/{cloud_id}/wiki/api/v2/...`. The v2 collections cursor-paginate: the
response carries the next page as a `_links.next` HATEOAS link whose query (`cursor`, `limit`) is
followed verbatim until it is absent. `pages`, `blog_posts`, and `comments` are incremental — each
record's `version.createdAt` is filtered past the stored watermark and lifted to a flat
`updated_at` the sync advances a cursor over; a page id is site-scoped (`{cloud_id}:{id}`) so two
sites never collide on one ref. `spaces` re-reads the whole set each run. A grant that lacks the
scope or was not shared the space (`401`/`403`) yields `StreamSkipped` so the run records a skip,
not a failure. The credential is resolved through the auth proxy the runner threads — this
connector holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from selfhost.sdk.sources import StreamSkipped
from selfhost_ext_sources.connector import StreamSpec
from selfhost_ext_sources.rest import RestConnector, get_path, list_or_empty

PAGE_SIZE = 100
_VERSION_CREATED = "version.createdAt"
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
}
_PARAMS: dict[str, dict[str, Any]] = {
    "spaces": {"description-format": "view"},
    "pages": {"body-format": "storage"},
    "blog_posts": {"body-format": "storage"},
    "comments": {"body-format": "storage"},
}

CONFLUENCE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="spaces", source_object="spaces", primary_key="id"),
    StreamSpec(name="pages", source_object="pages", primary_key="id", cursor_field="updated_at"),
    StreamSpec(
        name="blog_posts", source_object="blogposts", primary_key="id", cursor_field="updated_at"
    ),
    StreamSpec(
        name="comments",
        source_object="footer-comments",
        primary_key="id",
        cursor_field="updated_at",
        canonical=False,
    ),
]


class ConfluenceConnector(RestConnector):
    name = "confluence"
    base_url = "https://api.atlassian.com"
    streams_list = CONFLUENCE_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        suffix = _PATHS.get(stream.name)
        if suffix is None:
            raise NotImplementedError(
                f"confluence: stream {stream.name!r} has no paginate dispatch"
            )
        cursor_path = _VERSION_CREATED if stream.cursor_field else None
        try:
            for site in await self._sites(client):
                cloud_id = site.get("id")
                if not isinstance(cloud_id, str) or not cloud_id:
                    continue
                path = f"/ex/confluence/{cloud_id}/{suffix}"
                async for page in self._follow(
                    client,
                    path,
                    params=_PARAMS.get(stream.name),
                    cursor=cursor,
                    cursor_path=cursor_path,
                ):
                    yield [{**record, "cloud_id": cloud_id} for record in page]
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
        return list_or_empty(response.json()) if response.content else []

    async def _follow(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None,
        cursor: str | None,
        cursor_path: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk a v2 collection by following its `_links.next` cursor. An incremental stream
        filters each page past the stored watermark on `cursor_path` (Confluence has no server-side
        `since`), so a re-run reads all pages but lands only records newer than the cursor."""
        query: dict[str, Any] = {"limit": PAGE_SIZE, **(params or {})}
        while True:
            data = await self._get(client, path, params=query)
            records = list_or_empty(data.get("results"))
            if cursor and cursor_path:
                records = [r for r in records if str(get_path(r, cursor_path) or "") > cursor]
            if records:
                yield records
            following = _next_query(data)
            if following is None:
                return
            query = {"limit": PAGE_SIZE, **(params or {}), **following}

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Site-scope the record id so two sites never collide on one ref, and lift the nested
        `version.createdAt` into the flat `updated_at` the sync advances its watermark over."""
        flat = {key: value for key, value in record.items() if key != "cloud_id"}
        cloud_id = record.get("cloud_id")
        external_id = record.get(stream.primary_key)
        if isinstance(cloud_id, str) and external_id is not None:
            flat[stream.primary_key] = f"{cloud_id}:{external_id}"
        if stream.cursor_field:
            flat[stream.cursor_field] = get_path(record, _VERSION_CREATED)
        return flat

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "pages" | "blog_posts" | "comments":
                title = _str(record.get("title"))
                body = _StorageTextExtractor.extract(
                    get_path(record, "body.storage.value") or get_path(record, "body.view.value")
                )
            case "spaces":
                title = _str(record.get("name")) or _str(record.get("key"))
                body = _StorageTextExtractor.extract(
                    get_path(record, "description.view.value")
                    or get_path(record, "description.plain.value")
                )
            case _:
                return super().render(record, stream)
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


def _next_query(data: dict[str, Any]) -> dict[str, str] | None:
    """The query params of the `_links.next` HATEOAS link — Confluence v2 carries the opaque
    `cursor` there, so following the link's own params advances the page without minting the
    cursor. Absent or param-less means the last page."""
    following = get_path(data, "_links.next")
    if not isinstance(following, str) or not following:
        return None
    return dict(parse_qsl(urlsplit(following).query)) or None
