"""The source-grant edge table arrives holding the access every live source already had.

Enforcement reads `source_grant`, and registering a source is the only surface that grants one
going forward, so a migration that granted less than a workspace already had would revoke access to
every source registered before it with no path back. Every agent in the workspace could read every
source its audience covered, so every agent inherits a grant for every live source. The proof runs
the real `readable_source_ids` against the migrated database as an
automatic turn with no speaker — the reader that holds no exact-owner exception — for the main agent
and for a child agent alike; a removed source stays gone for both."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR, dispose_db, init_db
from ufo.runtime.ext.context import SourceReader, context_for
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws


@dataclass(frozen=True)
class _Seeded:
    config: Config
    path: Path
    workspace_id: UUID
    member_id: UUID
    main_agent_id: UUID
    child_agent_id: UUID
    shared_source_id: UUID
    private_source_id: UUID
    removed_source_id: UUID


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(path: Path, *, agents: bool) -> _Seeded:
    """A workspace as it stands the moment before 0059 runs: two live sources its agents have
    always read — one shared, one private to its owning member — plus a deleted one, and a second
    agent alongside the main one. With `agents` false the workspace holds no agent at all, so its
    live sources have nobody to inherit them."""
    config = _config(path)
    command.upgrade(config, "0058")
    workspace_id, member_id = uuid4(), uuid4()
    main_agent_id, child_agent_id = uuid4(), uuid4()
    shared_source_id, private_source_id, removed_source_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 27, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into member "
                "(id, workspace_id, email, is_admin, created_at, updated_at) "
                "values (:id, :workspace, 'owner@x.test', true, :now, :now)"
            ),
            {"id": member_id.hex, "workspace": workspace_id.hex, "now": now},
        )
        for agent_id, name, is_main in (
            ((main_agent_id, "ufo", True), (child_agent_id, "research", False)) if agents else ()
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, is_main, "
                    "internet_access_allowed, created_at, updated_at) "
                    "values (:id, :workspace, :name, 'p', 'm', :main, true, :now, :now)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace": workspace_id.hex,
                    "name": name,
                    "main": is_main,
                    "now": now,
                },
            )
        for source_id, subject, owner, removed_at in (
            (shared_source_id, SHARED_SUBJECT, None, None),
            (private_source_id, member_subject(member_id), member_id.hex, None),
            (removed_source_id, SHARED_SUBJECT, None, now),
        ):
            connection.execute(
                sa.text(
                    "insert into source "
                    "(id, workspace_id, backend, config, subject, owner_member_id, "
                    "next_sync_at, consecutive_errors, removed_at, created_at, updated_at) "
                    "values (:id, :workspace, 'folder', '{}', :subject, :owner, "
                    ":now, 0, :removed, :now, :now)"
                ),
                {
                    "id": source_id.hex,
                    "workspace": workspace_id.hex,
                    "subject": subject,
                    "owner": owner,
                    "removed": removed_at,
                    "now": now,
                },
            )
        connection.commit()
    engine.dispose()
    return _Seeded(
        config=config,
        path=path,
        workspace_id=workspace_id,
        member_id=member_id,
        main_agent_id=main_agent_id,
        child_agent_id=child_agent_id,
        shared_source_id=shared_source_id,
        private_source_id=private_source_id,
        removed_source_id=removed_source_id,
    )


async def _readable(seeded: _Seeded, agent_id: UUID) -> frozenset[UUID]:
    init_db(f"sqlite+aiosqlite:///{seeded.path}")
    try:
        with ws(seeded.workspace_id):
            return await context_for("probe", frozenset()).readable_source_ids(
                SourceReader(
                    agent_id=agent_id,
                    requesting_member_id=None,
                    subjects=frozenset({SHARED_SUBJECT, member_subject(seeded.member_id)}),
                )
            )
    finally:
        await dispose_db()


def test_source_grant_migration_keeps_every_live_source_readable(tmp_path: Path) -> None:
    seeded = _seed(tmp_path / "source-grants.db", agents=True)

    command.upgrade(seeded.config, "0059")

    engine = sa.create_engine(f"sqlite:///{seeded.path}")
    inspector = sa.inspect(engine)
    assert any(
        unique["column_names"] == ["workspace_id", "id"]
        for unique in inspector.get_unique_constraints("source")
    )
    assert {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("source_grant")
    } >= {
        ("workspace_id", "source_id"),
        ("workspace_id", "agent_id"),
    }
    with engine.connect() as connection:
        grants = connection.execute(
            sa.text(
                "select workspace_id, source_id, agent_id, created_at, updated_at from source_grant"
            )
        ).all()
    engine.dispose()
    assert {(UUID(row.source_id), UUID(row.agent_id)) for row in grants} == {
        (seeded.shared_source_id, seeded.main_agent_id),
        (seeded.private_source_id, seeded.main_agent_id),
        (seeded.shared_source_id, seeded.child_agent_id),
        (seeded.private_source_id, seeded.child_agent_id),
    }
    assert all(UUID(row.workspace_id) == seeded.workspace_id for row in grants)
    assert all(row.created_at is not None and row.updated_at is not None for row in grants)

    assert asyncio.run(_readable(seeded, seeded.main_agent_id)) == {
        seeded.shared_source_id,
        seeded.private_source_id,
    }
    assert asyncio.run(_readable(seeded, seeded.child_agent_id)) == {
        seeded.shared_source_id,
        seeded.private_source_id,
    }


def test_source_grant_migration_never_grants_across_workspaces(tmp_path: Path) -> None:
    """The backfill joins agents to sources within one workspace, never a cross product. A second
    workspace's agent must not inherit the first's sources — the workspace-scoped join is the whole
    boundary, so a source lands only on agents of its own workspace."""
    seeded = _seed(tmp_path / "cross.db", agents=True)
    other_workspace, other_agent, other_source = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 27, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{seeded.path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": other_workspace.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "internet_access_allowed, created_at, updated_at) "
                "values (:id, :ws, 'other', 'p', 'm', true, true, :now, :now)"
            ),
            {"id": other_agent.hex, "ws": other_workspace.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into source (id, workspace_id, backend, config, subject, owner_member_id, "
                "next_sync_at, consecutive_errors, removed_at, created_at, updated_at) "
                "values (:id, :ws, 'folder', '{}', :subject, null, :now, 0, null, :now, :now)"
            ),
            {
                "id": other_source.hex,
                "ws": other_workspace.hex,
                "subject": SHARED_SUBJECT,
                "now": now,
            },
        )
        connection.commit()
    engine.dispose()

    command.upgrade(seeded.config, "0059")

    engine = sa.create_engine(f"sqlite:///{seeded.path}")
    with engine.connect() as connection:
        grants = connection.execute(
            sa.text("select workspace_id, source_id, agent_id from source_grant")
        ).all()
    engine.dispose()
    pairs = {(UUID(row.source_id), UUID(row.agent_id)) for row in grants}
    assert (other_source, other_agent) in pairs
    assert (seeded.shared_source_id, other_agent) not in pairs
    assert (other_source, seeded.main_agent_id) not in pairs
    assert all(
        UUID(row.workspace_id)
        == (other_workspace if UUID(row.source_id) == other_source else seeded.workspace_id)
        for row in grants
    )


def test_source_grant_migration_refuses_a_live_source_no_agent_can_inherit(
    tmp_path: Path,
) -> None:
    """No source may silently disappear. A workspace holding live sources with no agent at all has
    nobody to inherit them, so the migration stops rather than revoking them."""
    seeded = _seed(tmp_path / "unreachable.db", agents=False)

    with pytest.raises(RuntimeError, match="no agent to hold their grant"):
        command.upgrade(seeded.config, "0059")


def test_source_grant_migration_grants_nothing_on_a_database_with_no_sources(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.db"

    command.upgrade(_config(path), "0059")

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert connection.execute(sa.text("select count(*) from source_grant")).scalar_one() == 0
    engine.dispose()
