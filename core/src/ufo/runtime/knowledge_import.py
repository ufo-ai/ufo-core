"""The move of a workspace's sources into the sources service, and the gate it holds: nothing reads
a workspace's feed, or connects its sources remotely, until its import is done, since the feed's
older pages are still arriving and a remote source would duplicate an imported one."""

from collections.abc import Collection
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import owner_tx
from ufo.runtime.ext.context import CORE_EXTENSION
from ufo.schema import tables

IMPORT_SOURCES_KEY = "import:sources"


class ImportCursor(BaseModel):
    """Where a workspace's sources import stands, in core's own `ext_store` key space."""

    model_config = ConfigDict(extra="forbid")
    done: bool = False


async def sources_imported(workspace_ids: Collection[UUID]) -> frozenset[UUID]:
    """The workspaces of `workspace_ids` whose sources import is done."""
    if not workspace_ids:
        return frozenset()
    async with owner_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.ext_store.c.workspace_id, tables.ext_store.c.value).where(
                    tables.ext_store.c.extension == CORE_EXTENSION,
                    tables.ext_store.c.key == IMPORT_SOURCES_KEY,
                    tables.ext_store.c.workspace_id.in_(list(workspace_ids)),
                )
            )
        ).all()
    return frozenset(
        row.workspace_id for row in rows if ImportCursor.model_validate(row.value).done
    )
