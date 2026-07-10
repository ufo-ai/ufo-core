"""Every workspace is one craft aloft: `/fleet`'s count reads the same `workspace` table both
onboarding tiers write, so the landing page's sky can never drift from the real fleet."""

import uuid

from ufo_control.gateway import craft_count
from ufo_control.gateway_store import OnboardStore


async def test_craft_count_counts_workspaces(store: OnboardStore) -> None:
    before = await craft_count(store.pool)
    async with store.pool.acquire() as connection:
        for _ in range(2):
            await connection.execute("insert into workspace (id) values ($1)", uuid.uuid4())
    assert await craft_count(store.pool) == before + 2
