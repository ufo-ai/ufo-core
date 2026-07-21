"""The `page` object kind: synced source pages projected as read-and-forget workspace objects.

A page is one document the core sync driver landed from a registered `source` — its identity is the
`page` row the driver owns, so names are the row id (`<uuid>`), id-shaped exactly as the grammar
admits. The kind is the read-and-forget surface over those rows: list and get read metadata through
the sanctioned `ExtensionContext.source_pages` accessor (the body stays by reference, never
inlined) scoped to the caller's own visibility subjects, and delete tombstones one page through
`forget_page` so the existing page-change pipeline reaps its derived index state. Pages are
produced by the sync driver, never authored, so create and update raise `VerbNotSupported`; delete
is owner-gated.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
    OBJECT_LIST_PAGE,
    ObjectKind,
    ObjectPage,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
)
from ufo.sdk.sources import SHARED_SUBJECT, member_subject
from ufo.sdk.tools import ToolContext

PAGE_KIND = "page"
PAGES_ARE_SYNCED = (
    "pages are landed by the content-sync driver, not authored — register a source to sync them"
)
PAGE_FORGET_GATE = "only the workspace owner can forget a synced page"
SUMMARY_MAX = 120


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(description="The content-source provider the page synced from.")
    subject: str = Field(description="The page's visibility subject: 'shared' or 'member:<id>'.")
    digest: str = Field(description="The content digest of the page body at last sync.")
    body_ref: str = Field(
        description="Reference to the page body in the blob store; the body is never inlined."
    )


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("page objects dispatched without their ExtensionContext")
    return ctx.ext


def _audience_subjects(ctx: ToolContext) -> frozenset[str]:
    """The visibility subjects the caller may read: the shared space plus, when the turn has an
    audience member, that member's private space — mirroring the memory/knowledge_graph readers so
    a member never reads another member's private page."""
    if ctx.audience_member_id is None:
        return frozenset({SHARED_SUBJECT})
    return frozenset({SHARED_SUBJECT, member_subject(ctx.audience_member_id)})


@dataclass(frozen=True)
class _Page:
    id: UUID
    source_id: UUID
    backend: str
    subject: str
    digest: str
    body_ref: str
    created_at: datetime
    updated_at: datetime

    @property
    def name(self) -> str:
        return str(self.id)

    def spec(self) -> PageSpec:
        return PageSpec(
            source=self.backend, subject=self.subject, digest=self.digest, body_ref=self.body_ref
        )

    def summary(self) -> str:
        return f"{self.backend} page ({self.subject}), {self.digest}"[:SUMMARY_MAX]


@dataclass(frozen=True)
class PageObjects:
    """The kind's handlers over the workspace's live (non-tombstoned) pages the caller may see:
    list and get read metadata through `source_pages`, joining each page to its source for the
    provider name via `sources()`; delete tombstones the row through `forget_page` so the
    page-change pipeline clears its derived index state. Only the workspace owner may forget a
    page."""

    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage:
        pages = [
            page for page in await self._pages(ctx) if query in page.name or query in page.summary()
        ]
        pages.sort(key=lambda page: page.name)
        remaining = [page for page in pages if page.name > cursor] if cursor else pages
        page_rows, rest = remaining[:OBJECT_LIST_PAGE], remaining[OBJECT_LIST_PAGE:]
        rows = tuple(ObjectRow(name=page.name, summary=page.summary()) for page in page_rows)
        return ObjectPage(rows=rows, next_cursor=page_rows[-1].name if rest else None)

    async def get(self, ctx: ToolContext, name: str) -> PageSpec | None:
        page = await self._find(ctx, name)
        return None if page is None else page.spec()

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        page = await self._find(ctx, name)
        if page is None:
            return None
        return {
            "source_id": str(page.source_id),
            "backend": page.backend,
            "created_at": page.created_at.isoformat(),
            "updated_at": page.updated_at.isoformat(),
        }

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
        backends = {source.id: source.backend for source in await ext.sources()}
        return tuple(
            _Page(
                id=record.id,
                source_id=record.source_id,
                backend=backends.get(record.source_id, ""),
                subject=record.subject,
                digest=record.digest,
                body_ref=record.body_ref,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in await ext.source_pages(_audience_subjects(ctx))
        )


PAGE_OBJECT = ObjectKind(
    name=PAGE_KIND,
    description=(
        "A synced source page: one document the content-sync driver landed from a registered "
        "source, read-only with an owner-only forget (delete). Created and updated only by the "
        "sync driver."
    ),
    guidance=(
        "List and get synced pages by kind and name (the page's row id); the spec carries the "
        "source provider, visibility subject, content digest, and a blob reference — the body is "
        "never inlined. Pages are landed by the content-sync driver, so create and update are "
        "refused; only the workspace owner can delete (forget) a page, which tombstones it and "
        "clears its derived index state. A source subscription's change alert references the "
        "changed pages by name so you can object_get them here."
    ),
    spec_model=PageSpec,
    store=PageObjects(),
)
