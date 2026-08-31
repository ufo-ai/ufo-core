"""The typed memory-search seam shared by extensions that provide and consume recall."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ufo.runtime.ext.context import SourceReader
from ufo.runtime.listings import ListingCursor, ListingPage
from ufo.runtime.object_name import ObjectRef

DEFAULT_MEMORY_SEARCH_PROVIDER = "default"


@dataclass(frozen=True)
class MemoryMatch:
    """One provider-neutral memory result ready for a consumer to inject. `ref` is the durable
    object behind the hit — search finds, `object_get` opens — and `created_at` is its recency,
    rendered beside the snippet so hits are triaged without opening them; both are None only for
    a provider whose results are not object-backed."""

    kind: str
    text: str
    ref: ObjectRef | None = None
    created_at: datetime | None = None
    subject: str | None = None


class MemorySearchProvider(Protocol):
    """A memory extension's workspace-ambient search implementation. `list_recent` is the
    browse half: the live memory items a subject set may read in recency order, no query and no
    similarity — source pages stay search-only, so it takes subjects rather than a reader. It
    pages by keyset (`cursor`), never by offset: items land while a member reads, and an offset
    would repeat or skip a row across that write. `kinds` narrows to item classes, and
    `listable_kinds` is the closed set a consumer offers — the provider's own classes, so a
    class it starts writing cannot go missing from the filter."""

    async def search(
        self,
        queries: tuple[str, ...],
        reader: SourceReader,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]: ...

    async def list_recent(
        self,
        subjects: frozenset[str],
        limit: int,
        kinds: frozenset[str] | None = None,
        cursor: ListingCursor | None = None,
    ) -> ListingPage[MemoryMatch]: ...

    def listable_kinds(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class MemorySearch:
    """Dispatch one exact readable subject set to the selected provider."""

    provider: MemorySearchProvider

    async def search(
        self,
        reader: SourceReader,
        queries: tuple[str, ...],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        return await self.provider.search(queries, reader, start, end)

    async def list_recent(
        self,
        subjects: frozenset[str],
        limit: int,
        kinds: frozenset[str] | None = None,
        cursor: ListingCursor | None = None,
    ) -> ListingPage[MemoryMatch]:
        return await self.provider.list_recent(subjects, limit, kinds, cursor)

    def listable_kinds(self) -> tuple[str, ...]:
        return self.provider.listable_kinds()
