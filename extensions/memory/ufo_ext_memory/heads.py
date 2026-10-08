"""What is true now about an overtaken memory, and the pointer an episodic match is served as."""

import asyncio
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from uuid import UUID

from ufo_ext_memory.client import Memory, MemoryApi, Reach

HEAD_WALK_MAX = 8
"""How many pointers recall follows from an overtaken row to the live head it quotes. A chain grows
by one per change of the world the row names, so eight is years of moves; a longer walk is a cycle
the stamp rule forbids, and stops here rather than looping."""


def topic_pointer(rank: int, memory_id: UUID) -> str:
    """The text an episodic match is served as: a breadcrumb to browse, never verbatim context,
    numbered by its 1-based `rank` in the answer it came from."""
    return f"Memory topic {rank} (item {memory_id})"


@dataclass(frozen=True)
class Heads:
    """The live memory at the end of each overtaken match's chain, read as the match's reader."""

    memory: MemoryApi

    async def of(
        self, matches: Sequence[Memory], subjects: Collection[str], reach: Reach | None
    ) -> dict[UUID, str]:
        """The body of the live memory each overtaken match's chain ends at, keyed by the match's
        id. Each level fetches every pending memory at once under `subjects` and `reach`, for at
        most `HEAD_WALK_MAX` levels; a memory the service does not show that reader, a retired
        one, or the cap ends that chain with no head."""
        heads: dict[UUID, str] = {}
        pending = {
            match.id: match.invalidated_by for match in matches if match.invalidated_by is not None
        }
        for _ in range(HEAD_WALK_MAX):
            if not pending:
                break
            targets = tuple(dict.fromkeys(pending.values()))
            found = await asyncio.gather(
                *(self.memory.get(target, subjects=subjects, reach=reach) for target in targets)
            )
            rows = dict(zip(targets, found, strict=True))
            following: dict[UUID, UUID] = {}
            for origin, target in pending.items():
                row = rows[target]
                if row is None or row.retired_at is not None:
                    continue
                onward = row.superseded_by or row.invalidated_by
                if onward is None:
                    heads[origin] = row.body
                else:
                    following[origin] = onward
            pending = following
        return heads
