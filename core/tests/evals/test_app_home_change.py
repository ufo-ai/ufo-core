from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from evals.suites import app_home_change
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.context import ScopedStore


async def _workspace() -> tuple[UUID, tuple[UUID, UUID]]:
    workspace_id = uuid4()
    agent_ids = (uuid4(), uuid4())
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": agent_id,
                    "workspace_id": workspace_id,
                    "name": name,
                    "prompt": "p",
                    "model": "claude-opus-4-8",
                    "is_main": is_main,
                    "created_at": now,
                    "updated_at": now,
                }
                for agent_id, name, is_main in (
                    (agent_ids[0], "assistant", True),
                    (agent_ids[1], "chat", False),
                )
            ],
        )
    return workspace_id, agent_ids


async def test_page_seeds_settle_homepage_jobs_for_every_agent(db: None, tmp_path) -> None:
    workspace_id, agent_ids = await _workspace()
    keys = tuple(f"{app_home_change.HOMEPAGE_SETTLED_PREFIX}{agent_id}" for agent_id in agent_ids)
    with ws(workspace_id):
        await app_home_change._rowless_page()(
            workspace_id,
            agent_ids[0],
            FilesystemBlobStore(root=tmp_path),
        )
        values = await ScopedStore(extension=app_home_change.EXTENSION_WEB).get_many(keys)

    assert values == dict.fromkeys(keys, "eval")
