import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from ufo.db import MIGRATIONS_DIR, apply_migrations, workspace_tx
from ufo.ext.loader import migration_locations
from ufo.schema import tables


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


def test_extension_migration_forms_one_head_per_owner(database_url: str) -> None:
    """The migration seam's schema invariant: each table-owning extension's version location layers
    over core's — `apply_migrations` ran clean in the fixture — and the graph has exactly one head
    per owner (core's chain plus each extension branch), so `upgrade heads` is deterministic,
    core-first. The base-pinned index_default and memory extensions own their chunk and memory_item
    tables, the sample probe owns its note table, skill_create owns the user_skill table,
    knowledge-graph owns the graph_entity and graph_edge tables, and eval_env owns the fake
    mailbox and calendar tables."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    heads = set(ScriptDirectory.from_config(config).get_heads())
    assert {
        "index_default_0002",
        "memory_0005",
        "sample_ext_note_0001",
        "skill_create_0001",
        "knowledge_graph_0001",
        "eval_env_0001",
    } <= heads
    assert len(heads) == 7


def test_migrate_command_brings_the_schema_to_head(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator verb behind the extension-migration seam: `ufoctl migrate` applies core's
    schema plus every active extension's branch, so `ext install` → `migrate` → `serve` actually
    creates a table-owning extension's tables. Idempotent, so it is safe to re-run."""
    from click.testing import CliRunner

    from ufo.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ufo.toml").write_text(
        '[database]\nurl = "sqlite+aiosqlite:///app.db"\n'
        '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
    )
    runner = CliRunner()
    assert runner.invoke(main, ["migrate"]).exit_code == 0
    assert runner.invoke(main, ["migrate"]).exit_code == 0

    engine = sa.create_engine("sqlite:///" + str(tmp_path / "app.db"))
    try:
        with engine.connect() as connection:
            names = set(sa.inspect(connection).get_table_names())
    finally:
        engine.dispose()
    assert "workspace" in names
    assert "user_skill" in names
    assert "sample_ext_note" in names


async def test_workspace_tx_round_trip(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.workspace.c.id).where(tables.workspace.c.id == workspace_id)
            )
        ).one()
    assert row.id == workspace_id


async def test_workspace_tx_requires_init() -> None:
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass


async def test_turn_protocol_state_is_constrained(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=1,
                        status="queued",
                        inbound="hi",
                        admission_source="typo",
                        terminal=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=1,
                        status="done",
                        inbound="hi",
                        terminal=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
