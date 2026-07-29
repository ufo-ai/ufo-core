"""The typed memory-search seam shared by extensions that provide and consume recall."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ufo.ext.context import SourceReader
from ufo.objects import ObjectRef

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


class MemorySearchProvider(Protocol):
    """A memory extension's workspace-ambient search implementation."""

    async def search(
        self,
        queries: tuple[str, ...],
        reader: SourceReader,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]: ...


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
