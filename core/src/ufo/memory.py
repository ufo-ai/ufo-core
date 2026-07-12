"""The typed memory-search seam shared by extensions that provide and consume recall."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws_current

DEFAULT_MEMORY_SEARCH_PROVIDER = "default"


@dataclass(frozen=True)
class MemoryMatch:
    """One provider-neutral memory result ready for a consumer to inject."""

    kind: str
    text: str


class MemorySearchProvider(Protocol):
    """A memory extension's workspace-ambient search implementation."""

    async def search(
        self,
        queries: tuple[str, ...],
        member_id: UUID | None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]: ...


@dataclass(frozen=True)
class MemorySearch:
    """Resolve a conversation's member before dispatching scoped provider search."""

    provider: MemorySearchProvider

    async def search(
        self,
        conversation_id: UUID,
        queries: tuple[str, ...],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        """Search the conversation member's private and shared memory."""
        async with workspace_tx() as connection:
            member_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.workspace_id == ws_current().workspace_id,
                        tables.conversation.c.id == conversation_id,
                    )
                )
            ).scalar_one()
        return await self.provider.search(queries, member_id, start, end)
