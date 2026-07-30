"""Keyset paging for the portal's listings — one ordering, one cursor, one page envelope.

A listing pages by keyset, never by offset: rows land while a member reads, and an offset would
repeat or skip a row across that write. Every listing therefore orders the same way — newest
first, `created_at DESC` with the row id breaking its ties — so one cursor value names a position
in any of them, and one page envelope carries the rows beside the positions its controls walk to.

This is the shared home the memory listing and the artifacts listing both reach for: the memory
provider imports it through `ufo.sdk.listings`, a core-side read imports it directly, and neither
owns the arithmetic. A consumer supplies its two ordering columns and how to read a cursor off one
of its rows; `page_query` and `page_of` do the rest."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

CURSOR_SEPARATOR = "|"
CURSOR_NEWER = "newer"
CURSOR_OLDER = "older"


class MalformedCursor(ValueError):
    """A token that names no position. A surface answers its client's error rather than silently
    serving the newest page — a member on a stale link is told, not quietly moved."""


@dataclass(frozen=True)
class ListingCursor:
    """One row's position in the listing ordering, `(created_at, item_id)` — both, because
    `created_at` alone repeats and a cursor that cannot break its own tie repeats or skips a row
    at the page boundary. `newer` says which side of the position the page wants: listings run
    newest-first, so paging forward asks for what is older."""

    created_at: datetime
    item_id: str
    newer: bool = False

    def encode(self) -> str:
        """The cursor as one query-string token."""
        side = CURSOR_NEWER if self.newer else CURSOR_OLDER
        return CURSOR_SEPARATOR.join((side, self.created_at.isoformat(), self.item_id))

    @classmethod
    def decode(cls, token: str) -> "ListingCursor":
        """The position a token names, or `MalformedCursor` — a surface that minted no such token
        refuses it rather than guessing which page the member meant."""
        side, _, position = token.partition(CURSOR_SEPARATOR)
        stamp, _, item_id = position.partition(CURSOR_SEPARATOR)
        if side not in (CURSOR_NEWER, CURSOR_OLDER) or not stamp or not item_id:
            raise MalformedCursor(token)
        try:
            created_at = datetime.fromisoformat(stamp)
            UUID(item_id)
        except ValueError as error:
            raise MalformedCursor(token) from error
        return cls(created_at=created_at, item_id=item_id, newer=side == CURSOR_NEWER)


@dataclass(frozen=True)
class ListingPage[RowT]:
    """One page of a listing with the positions its controls walk to. A boundary cursor is None
    exactly when no row lies that way, so the same value carries the position and whether the
    control exists — a listing at the newest end has no `newer`."""

    rows: tuple[RowT, ...]
    older: ListingCursor | None = None
    newer: ListingCursor | None = None


def page_query(
    query: sa.Select[Any],
    cursor: ListingCursor | None,
    limit: int,
    *,
    created_at: sa.ColumnElement[datetime],
    ident: sa.ColumnElement[Any],
) -> sa.Select[Any]:
    """The caller's query ordered and bounded for one keyset page: newest-first by default, the
    ordering inverted while walking toward newer rows (the page is reversed back in `page_of`),
    and one row past the limit so a full last page is distinguishable from a page with more
    behind it."""
    ordering = (
        (created_at.asc(), ident.asc())
        if cursor is not None and cursor.newer
        else (created_at.desc(), ident.desc())
    )
    paged = query.order_by(*ordering).limit(limit + 1)
    if cursor is None:
        return paged
    edge = (cursor.created_at, UUID(cursor.item_id))
    position = sa.tuple_(created_at, ident)
    return paged.where(position > edge if cursor.newer else position < edge)


def page_of[RowT, SourceT](
    rows: Sequence[SourceT],
    cursor: ListingCursor | None,
    limit: int,
    *,
    render: Callable[[SourceT], RowT],
    position: Callable[[SourceT], tuple[datetime, str]],
) -> ListingPage[RowT]:
    """One `ListingPage` from what `page_query` returned: the extra row decides whether the walked
    side has more, and the opposite side has more whenever the page was reached from a cursor at
    all — arriving from somewhere means something lies back that way."""
    beyond = len(rows) > limit
    page = list(rows[:limit])
    walking_newer = cursor is not None and cursor.newer
    if walking_newer:
        page.reverse()
    if not page:
        return ListingPage(rows=())

    def at(source: SourceT, *, newer: bool) -> ListingCursor:
        created_at, item_id = position(source)
        return ListingCursor(created_at=created_at, item_id=item_id, newer=newer)

    has_older = True if walking_newer else beyond
    has_newer = beyond if walking_newer else cursor is not None
    return ListingPage(
        rows=tuple(render(source) for source in page),
        older=at(page[-1], newer=False) if has_older else None,
        newer=at(page[0], newer=True) if has_newer else None,
    )
