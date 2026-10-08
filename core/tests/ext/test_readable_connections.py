from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import self_user_id_resolvers
from ufo.runtime.access.grants import FeedConnection
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.ext.surface import SurfaceIdentityContext, SurfaceSpec
from ufo.runtime.sources.sync import FOLDER_BACKEND, feed_handle_for
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    stranger_id: UUID
    main_agent_id: UUID
    granted_agent_id: UUID
    ungranted_agent_id: UUID
    private_connection_id: UUID
    shared_connection_id: UUID


async def _seeded() -> _Workspace:
    state = _Workspace(*(uuid4() for _ in range(8)))
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=state.workspace_id, created_at=now, updated_at=now
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": member_id,
                    "workspace_id": state.workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "created_at": now,
                    "updated_at": now,
                }
                for member_id in (state.owner_id, state.stranger_id)
            ],
        )
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": agent_id,
                    "workspace_id": state.workspace_id,
                    "name": name,
                    "prompt": "p",
                    "model": "m",
                    "is_main": is_main,
                    "created_at": now,
                    "updated_at": now,
                }
                for agent_id, name, is_main in (
                    (state.main_agent_id, "ufo", True),
                    (state.granted_agent_id, "research", False),
                    (state.ungranted_agent_id, "scout", False),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.connection),
            [
                {
                    "id": connection_id,
                    "workspace_id": state.workspace_id,
                    "provider": FOLDER_BACKEND,
                    "account_id": account_id,
                    "host": "",
                    "owner_member_id": owner_member_id,
                    "shared": shared,
                    "created_at": now,
                    "updated_at": now,
                }
                for connection_id, account_id, owner_member_id, shared in (
                    (state.private_connection_id, "owned", state.owner_id, False),
                    (state.shared_connection_id, "", None, True),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                agent_id=state.granted_agent_id,
                connection_id=state.private_connection_id,
                created_at=now,
                updated_at=now,
            )
        )
    return state


async def _grant(state: _Workspace, agent_id: UUID, connection_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def _reader(
    state: _Workspace,
    agent_id: UUID,
    speaker_id: UUID | None,
    subjects: frozenset[str] | None = None,
) -> SourceReader:
    return SourceReader(
        agent_id=agent_id,
        requesting_member_id=speaker_id,
        subjects=(
            frozenset({SHARED_SUBJECT, member_subject(state.owner_id)})
            if subjects is None
            else subjects
        ),
    )


async def _readable(state: _Workspace, reader: SourceReader) -> frozenset[UUID]:
    with ws(state.workspace_id):
        return await context_for("probe", frozenset()).readable_connections(reader)


async def test_a_granted_agent_reaches_a_shared_connection() -> None:
    state = await _seeded()
    await _grant(state, state.ungranted_agent_id, state.shared_connection_id)

    assert await _readable(state, _reader(state, state.ungranted_agent_id, None)) == {
        state.shared_connection_id
    }


async def test_the_main_agent_reaches_shared_connections_and_the_speakers_private_one() -> None:
    state = await _seeded()

    assert await _readable(state, _reader(state, state.main_agent_id, state.owner_id)) == {
        state.private_connection_id,
        state.shared_connection_id,
    }
    assert await _readable(state, _reader(state, state.main_agent_id, None)) == {
        state.shared_connection_id
    }
    assert await _readable(state, _reader(state, state.main_agent_id, state.stranger_id)) == {
        state.shared_connection_id
    }


async def test_a_private_connection_needs_its_owners_subject() -> None:
    state = await _seeded()
    shared_only = frozenset({SHARED_SUBJECT})

    assert await _readable(
        state, _reader(state, state.main_agent_id, state.owner_id, shared_only)
    ) == {state.shared_connection_id}
    assert (
        await _readable(state, _reader(state, state.granted_agent_id, None, shared_only))
        == frozenset()
    )
    assert await _readable(
        state,
        _reader(state, state.granted_agent_id, None, frozenset({member_subject(state.owner_id)})),
    ) == {state.private_connection_id}


async def test_a_non_main_agent_without_a_grant_reaches_nothing() -> None:
    state = await _seeded()

    assert (
        await _readable(state, _reader(state, state.ungranted_agent_id, state.owner_id))
        == frozenset()
    )
    assert await _readable(state, _reader(state, state.ungranted_agent_id, None)) == frozenset()


async def test_readable_connections_equals_the_sources_fence_today() -> None:
    state = await _seeded()
    now = datetime.now(UTC)
    connection_of = {uuid4(): state.private_connection_id, uuid4(): state.shared_connection_id}
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source),
            [
                {
                    "id": uuid4(),
                    "uid": source_id,
                    "workspace_id": state.workspace_id,
                    "backend": FOLDER_BACKEND,
                    "config": {"root": f"/{source_id.hex}"},
                    "feed_handle": feed_handle_for({"root": f"/{source_id.hex}"}, frozenset()),
                    "connection_id": connection_id,
                    "cursor": None,
                    "next_sync_at": now,
                    "claimed_by": None,
                    "claim_expires_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for source_id, connection_id in connection_of.items()
            ],
        )

    for agent_id, speaker_id in (
        (state.main_agent_id, state.owner_id),
        (state.main_agent_id, None),
        (state.main_agent_id, state.stranger_id),
        (state.ungranted_agent_id, state.owner_id),
        (state.granted_agent_id, None),
    ):
        reader = _reader(state, agent_id, speaker_id)
        with ws(state.workspace_id):
            ext = context_for("probe", frozenset())
            fenced = {
                connection_of[source_id] for source_id in await ext.readable_source_ids(reader)
            }
            assert await ext.readable_connections(reader) == fenced


@pytest.mark.parametrize(
    ("account_id", "account"),
    [
        ("", None),
        (f"member:{UUID(int=7)}", None),
        ('{"root": "/notes"}', None),
        ("ca_9x2", "ca_9x2"),
    ],
)
def test_broker_account_reads_the_handle(account_id: str, account: str | None) -> None:
    connection = FeedConnection(
        id=uuid4(),
        provider="github",
        label="GitHub",
        account_id=account_id,
        base_url=None,
        backfill_days=None,
        owner_member_id=None,
        shared=True,
    )

    assert connection.broker_account == account


async def test_self_user_id_asks_the_named_surfaces_resolver(tmp_path: Path) -> None:
    state = await _seeded()

    async def speaker(ctx: SurfaceIdentityContext) -> str | None:
        return f"U{ctx.workspace_id.hex}"

    manifest = Manifest(
        name="chat", version="1", surfaces=(SurfaceSpec(name="chat", self_user_id=speaker),)
    )
    ext = context_for(
        "probe",
        frozenset(),
        self_user_ids=self_user_id_resolvers(
            (manifest,), None, WorkspaceBlobStore(backend=FilesystemBlobStore(tmp_path))
        ),
    )

    with ws(state.workspace_id):
        assert await ext.self_user_id("chat") == f"U{state.workspace_id.hex}"
        assert await ext.self_user_id("email") is None
