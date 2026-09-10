"""Forget the eval workspace's memories before a routing case runs.

A workspace that remembers how earlier trials built their apps stops loading the skill that
carries the guardrails, and the case then grades recall instead of routing. Recall's fusion loads
its hits by row id, so the index chunks a deleted item leaves behind recall nothing."""

import sqlalchemy as sa
from ufo_ext_memory.store import memory_item

from ufo.db import workspace_tx
from ufo.runtime.workspace import ws_current


async def forget_workspace_memory() -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(memory_item).where(memory_item.c.workspace_id == ws_current().workspace_id)
        )
