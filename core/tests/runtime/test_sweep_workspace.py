from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import EnqueueOptions

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.runtime.jobs import TurnDispatcher
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    register_sources,
)
from ufo.runtime.workspace import NoWorkspace, SeveralWorkspaces, sole_workspace, ws
from ufo.schema import tables


@dataclass
class _Enqueued:
    turns: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        self.turns.append(turn_id)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _queued_turn(workspace_id: UUID) -> UUID:
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    stale = datetime.now(UTC) - timedelta(hours=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-5-5",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="hello",
                created_at=stale,
                updated_at=stale,
            )
        )
    return turn_id


async def _dispatch_stamps() -> dict[UUID, datetime | None]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.dispatch_enqueued_at)
            )
        ).all()
    return {row.id: row.dispatch_enqueued_at for row in rows}


async def test_turn_dispatch_offers_and_stamps_only_the_bound_workspaces_turns(db: None) -> None:
    mine, theirs = await _workspace(), await _workspace()
    my_turn, their_turn = await _queued_turn(mine), await _queued_turn(theirs)
    enqueued = _Enqueued()

    with ws(mine):
        await TurnDispatcher(client=enqueued).run()

    assert enqueued.turns == [str(my_turn)]
    stamps = await _dispatch_stamps()
    assert stamps[my_turn] is not None
    assert stamps[their_turn] is None


async def _source_rows() -> dict[UUID, sa.Row]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.source.c.workspace_id,
                    tables.source.c.claimed_by,
                    tables.source.c.synced_at,
                )
            )
        ).all()
    return {row.workspace_id: row for row in rows}


async def _page_workspaces() -> list[UUID]:
    async with workspace_tx() as connection:
        return list((await connection.execute(sa.select(tables.page.c.workspace_id))).scalars())


async def test_source_sync_claims_writes_and_replays_only_the_bound_workspace(
    db: None, database_url: str, tmp_path: Path
) -> None:
    mine, theirs = await _workspace(), await _workspace()
    for workspace_id in (mine, theirs):
        root = tmp_path / str(workspace_id)
        root.mkdir()
        (root / "notes.md").write_text(f"notes of {workspace_id}")
        with ws(workspace_id):
            await register_sources(
                (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
            )
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )

    with ws(mine):
        await driver.run()

    sources = await _source_rows()
    assert sources[mine].synced_at is not None
    assert (sources[theirs].claimed_by, sources[theirs].synced_at) == (None, None)
    assert await _page_workspaces() == [mine]

    with ws(theirs):
        await driver.run()
        replayed = await CorePageFeed(blob=blob).pages_changed_since(None, 10)

    assert sorted(await _page_workspaces()) == sorted((mine, theirs))
    assert [change.body for change in replayed.changes] == [f"notes of {theirs}"]


async def test_an_act_naming_no_workspace_takes_the_databases_one_and_refuses_otherwise(
    db: None,
) -> None:
    with pytest.raises(NoWorkspace):
        await sole_workspace()
    only = await _workspace()
    assert await sole_workspace() == only
    await _workspace()
    with pytest.raises(SeveralWorkspaces, match="serves 2 workspaces"):
        await sole_workspace()
