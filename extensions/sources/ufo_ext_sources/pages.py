"""The `page` object kind: synced source pages projected as read-and-forget workspace objects.

A page is one document the core sync driver landed from a registered `source` — its identity is the
`page` row the driver owns, so names are the row id (`<uuid>`), id-shaped exactly as the grammar
admits. The kind is the read-and-forget surface over those rows: list and get read browse metadata
through the sanctioned `ExtensionContext.source_pages` accessor and get reads the body through the
turn's blob capability, scoped to the caller's own visibility subjects; delete tombstones one page
through `forget_page` so the existing page-change pipeline reaps its derived index state. Pages are
produced by the sync driver, never authored, so create and update raise `VerbNotSupported`; delete
is owner-gated.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.audience import audience_subjects
from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
    object_page,
)
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.sdk.tools import ToolContext
from ufo_ext_sources.registry import CONNECTORS, SOURCE_KIND, binding_name

PAGE_KIND = "page"
PAGES_ARE_SYNCED = (
    "pages are landed by the content-sync driver, not authored — register a source to sync them"
)
PAGE_FORGET_GATE = "only the workspace owner can forget a synced page"
SUMMARY_MAX = 120
PAGE_BODY_MAX_BYTES = 65_536


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(description="The stable id of the source binding that landed the page.")
    source: str = Field(description="The content-source provider the page synced from.")
    stream: str = Field(description="The provider stream, such as issues or pull_requests.")
    title: str = Field(description="The page's human-readable title.")
    created_at: str = Field(description="The provider creation time, or first sync time.")
    updated_at: str = Field(description="The provider update time, or last content change time.")
    subject: str = Field(description="The page's visibility subject: 'shared' or 'member:<id>'.")
    digest: str = Field(description="The content digest of the page body at last sync.")
    body_ref: str = Field(description="Reference to the page body in the blob store.")
    body: str = Field(description="The page body, bounded to the first 65,536 UTF-8 bytes.")
    body_truncated: bool = Field(description="Whether the body exceeded the object read bound.")


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("page objects dispatched without their ExtensionContext")
    return ctx.ext


def _page_timestamp(provider_value: str | None, row_value: datetime) -> str:
    if provider_value is None:
        parsed = row_value if row_value.tzinfo is not None else row_value.replace(tzinfo=UTC)
    else:
        try:
            parsed = datetime.fromisoformat(provider_value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid page timestamp {provider_value!r}") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"page timestamp lacks a timezone: {provider_value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class _Page:
    id: UUID
    source_id: UUID
    backend: str
    stream: str
    title: str
    record_created_at: str | None
    record_updated_at: str | None
    subject: str
    digest: str
    body_ref: str
    created_at: datetime
    updated_at: datetime
    source_name: str | None

    @property
    def name(self) -> str:
        return str(self.id)

    def links(self) -> tuple[ObjectLink, ...]:
        if self.source_name is None:
            return ()
        return (
            ObjectLink(
                relation="synced_by",
                target=ObjectRef(kind=SOURCE_KIND, name=self.source_name),
            ),
        )

    def spec(self, body: str, body_truncated: bool) -> PageSpec:
        return PageSpec(
            source_id=str(self.source_id),
            source=self.backend,
            stream=self.stream,
            title=self.title,
            created_at=_page_timestamp(self.record_created_at, self.created_at),
            updated_at=_page_timestamp(self.record_updated_at, self.updated_at),
            subject=self.subject,
            digest=self.digest,
            body_ref=self.body_ref,
            body=body,
            body_truncated=body_truncated,
        )

    def summary(self) -> str:
        return f"{self.title} — {self.backend}/{self.stream} ({self.subject})"[:SUMMARY_MAX]

    def fields(self) -> dict[str, JsonValue]:
        return {
            "source_id": str(self.source_id),
            "source": self.backend,
            "stream": self.stream,
            "title": self.title,
            "created_at": _page_timestamp(self.record_created_at, self.created_at),
            "updated_at": _page_timestamp(self.record_updated_at, self.updated_at),
        }


@dataclass(frozen=True)
class PageObjects:
    """The kind's handlers over the workspace's live (non-tombstoned) pages the caller may see:
    list and get read metadata through `source_pages`, joining each page to its source for the
    provider name via `sources()`; delete tombstones the row through `forget_page` so the
    page-change pipeline clears its derived index state. Only the workspace owner may forget a
    page."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = tuple(
            ObjectRow(name=page.name, summary=page.summary(), fields=page.fields())
            for page in await self._pages(ctx)
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None:
        page = await self._find(ctx, name)
        if page is None:
            return None
        chunks: list[bytes] = []
        size = 0
        stream = ctx.blob.get_stream(page.body_ref)
        try:
            async for chunk in stream:
                remaining = PAGE_BODY_MAX_BYTES + 1 - size
                chunks.append(chunk[:remaining])
                size += len(chunk[:remaining])
                if size > PAGE_BODY_MAX_BYTES:
                    break
        finally:
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()
        content = b"".join(chunks)
        bounded = content[:PAGE_BODY_MAX_BYTES]
        try:
            body = bounded.decode("utf-8")
        except UnicodeDecodeError as error:
            if len(content) <= PAGE_BODY_MAX_BYTES or error.reason != "unexpected end of data":
                raise
            body = bounded[: error.start].decode("utf-8")
        return ObjectDetail(
            spec=page.spec(body, len(content) > PAGE_BODY_MAX_BYTES),
            created_at=page.created_at,
            updated_at=page.updated_at,
            links=page.links(),
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None
    ) -> None:
        raise VerbNotSupported(PAGES_ARE_SYNCED)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        if not await ctx.speaker_is_owner():
            raise OwnerRequired(PAGE_FORGET_GATE)
        page = await self._find(ctx, name)
        if page is None:
            raise ValueError(f"no page named {name!r}")
        await _require_ext(ctx).forget_page(page.id)

    async def _find(self, ctx: ToolContext, name: str) -> _Page | None:
        return next((page for page in await self._pages(ctx) if page.name == name), None)

    async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]:
        ext = _require_ext(ctx)
        sources = await ext.sources()
        backends = {source.id: source.backend for source in sources}
        source_names = {
            source.id: binding_name(source.backend, config.account, config.base_url)
            for source in sources
            if source.backend in CONNECTORS
            and (config := ConnectorSourceConfig.model_validate(source.config))
        }
        return tuple(
            _Page(
                id=record.id,
                source_id=record.source_id,
                backend=backends.get(record.source_id, ""),
                stream=record.stream,
                title=record.title,
                record_created_at=record.record_created_at,
                record_updated_at=record.record_updated_at,
                subject=record.subject,
                digest=record.digest,
                body_ref=record.body_ref,
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_name=source_names.get(record.source_id),
            )
            for record in await ext.source_pages(audience_subjects(ctx.audience))
        )


PAGE_OBJECT = ObjectKind(
    name=PAGE_KIND,
    description=(
        "A synced source page: one document the content-sync driver landed from a registered "
        "source, read-only with an owner-only forget (delete). Created and updated only by the "
        "sync driver."
    ),
    guidance=(
        "List synced pages with exact `filters` on source_id, source, stream, title, created_at, "
        "or updated_at and `order_by` any of those fields; for example, filter one source's issues "
        "and order by created_at desc. Get by name returns those fields plus a bounded page body. "
        "Pages are landed by the content-sync driver, so create and update are "
        "refused; only the workspace owner can delete (forget) a page, which tombstones it and "
        "clears its derived index state. A source subscription's change alert references the "
        "changed pages by name so you can object_get them here."
    ),
    spec_model=PageSpec,
    store=PageObjects(),
    list_fields=frozenset({"source_id", "source", "stream", "title", "created_at", "updated_at"}),
)
