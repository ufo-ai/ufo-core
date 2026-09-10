"""The mirror row a page leaves behind, the key that collects it, and the revision it must name.

`mem_page` is derived state whose referent is a core `page` row. Disconnecting a connection deletes
that row outright — its source rows and their pages follow the connection by cascade, with no
tombstone in between — so without a foreign key the mirror survives the page it mirrors and nothing
else ever collects it: the page pass reads mirror rows and can no longer find that page, and no
event names it. Its `revision` is the claim that the indexed chunks belong to that revision of the
body, and `search_sources` serves a page only where the claim matches the live one, so a mirror that
makes no claim is one nothing may serve."""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from ufo_ext_memory.store import mem_page

from ufo.db import workspace_tx
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite", "postgresql"], indirect=True),
]

DIGEST = "sha256:page"


async def _seed(workspace_id: UUID) -> tuple[UUID, UUID]:
    source_uid = uuid7()
    connection_id, page_uid = uuid4(), uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="folder",
                account_id="",
                host="",
                owner_member_id=None,
                shared=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_uid,
                workspace_id=workspace_id,
                source_uid=source_uid,
                digest=DIGEST,
                body_ref=f"pages/{page_uid}",
                subject="shared",
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_uid)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_uid=page_uid,
                workspace_id=workspace_id,
                subject="shared",
                revision=revision,
                created_at=sa.func.now(),
            )
        )
    return connection_id, page_uid


async def _mirrors(workspace_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(mem_page.c.page_uid).where(mem_page.c.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )


async def test_deleting_a_page_takes_its_mirror_row(db: None) -> None:
    workspace_id = uuid4()
    _connection_id, page_uid = await _seed(workspace_id)
    with ws(workspace_id):
        assert await _mirrors(workspace_id) == [page_uid]
        async with workspace_tx() as connection:
            await connection.execute(sa.delete(tables.page).where(tables.page.c.uid == page_uid))
        assert await _mirrors(workspace_id) == []


async def test_a_mirror_row_cannot_be_written_without_a_revision(db: None) -> None:
    """The column carries what the writer has always produced. A mirror with no revision makes no
    claim about which body its chunks came from, and `search_sources` could never serve it — so the
    schema refuses it rather than holding a row nothing can return."""
    workspace_id = uuid4()
    _connection_id, page_uid = await _seed(workspace_id)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(sa.delete(mem_page))
        with pytest.raises(IntegrityError):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(mem_page).values(
                        page_uid=page_uid,
                        workspace_id=workspace_id,
                        subject="shared",
                        revision=None,
                        created_at=sa.func.now(),
                    )
                )
        assert await _mirrors(workspace_id) == []


async def test_disconnecting_a_connection_takes_the_mirror_rows_of_its_pages(db: None) -> None:
    """The whole chain a member drives: disconnect removes the connection, its source rows and
    their pages follow, and the mirror row goes with the page rather than outliving it."""
    workspace_id = uuid4()
    connection_id, page_uid = await _seed(workspace_id)
    with ws(workspace_id):
        assert await _mirrors(workspace_id) == [page_uid]
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.connection).where(tables.connection.c.id == connection_id)
            )
        assert await _mirrors(workspace_id) == []
