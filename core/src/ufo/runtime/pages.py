"""The page feed a `page_change` consumer reads: every page changed since a `revision|page_id`
cursor, bodies inlined, in a bounded batch and the feed's total order, so a single-owner cursor
advances monotonically and a restart resumes where it left off."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

PAGE_FEED_BATCH_MAX = 50


@dataclass(frozen=True)
class PageChange:
    """One page's current state as the feed replays it: the source it belongs to, the core
    connection that source syncs and its provider, the `stream` and `title` it landed under, the
    inlined body (empty when tombstoned), the content digest, monotonic revision, and `as_of` — the
    provider's update or creation time, falling back to ingestion time. `created_at == changed_at`
    marks a page this replay adds rather than updates. `indexed` is the stream's declaration of
    whether the page reaches memory."""

    page_id: UUID
    source_id: UUID
    connection_id: UUID
    provider: str
    subject: str
    stream: str
    title: str
    body: str
    digest: str
    revision: int
    tombstone: bool
    indexed: bool
    created_at: datetime
    as_of: datetime
    changed_at: datetime


@dataclass(frozen=True)
class PageBatch:
    changes: tuple[PageChange, ...]
    next_cursor: str | None


class PageFeed(Protocol):
    """The page-substrate seam a `page_change` consumer reads through: replay every page changed
    since a `revision|page_id` cursor in a bounded batch. `next_cursor` is where the next read
    starts, set on a batch that moved the position even when it kept no change; null holds it."""

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch: ...


def page_cursor(cursor: object) -> tuple[int, UUID]:
    """A `revision|page_id` cursor as the position it names; `ValueError` on any other value."""
    if not isinstance(cursor, str):
        raise ValueError("page cursor must be a string")
    revision, separator, page_id = cursor.partition("|")
    if not separator or not revision.isdecimal():
        raise ValueError(f"invalid page cursor {cursor!r}")
    try:
        return int(revision), UUID(page_id)
    except ValueError as error:
        raise ValueError(f"invalid page cursor {cursor!r}") from error
