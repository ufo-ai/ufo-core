import os
import warnings
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from ufo.db import MIGRATIONS_DIR, apply_migrations, workspace_tx
from ufo.ext.loader import migration_locations
from ufo.schema import tables


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


DUPLICATE_PROBE = """\"\"\"duplicate revision probe\"\"\"

revision: str = "probe_dup"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def test_apply_migrations_rejects_a_stamp_short_of_the_graph_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two files claiming one revision id are one graph node, so alembic stamps one head where the
    graph declares two and `upgrade heads` still exits 0. The run must not report success on a
    schema missing the losing file's DDL."""
    probe = tmp_path / "probe_versions"
    probe.mkdir()
    for name in ("first", "second"):
        (probe / f"{name}.py").write_text(DUPLICATE_PROBE)
    monkeypatch.setattr("ufo.ext.loader.migration_locations", lambda pack: (str(probe),))
    with pytest.raises(RuntimeError, match="collapses into one node"):
        apply_migrations(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")


def test_extension_migration_forms_one_head_per_owner(database_url: str) -> None:
    """The migration seam's schema invariant: each table-owning extension's version location layers
    over core's — `apply_migrations` ran clean in the fixture — and the graph has exactly one head
    per owner (core's chain plus each extension branch), so `upgrade heads` is deterministic,
    core-first. The base-pinned index_default and memory extensions own their chunk and memory_item
    tables, the sample probe owns its note table, skill_create owns the user_skill table,
    knowledge-graph owns the graph_entity and graph_edge tables, and eval_env owns the fake
    mailbox and calendar tables.

    Every revision id is unique: two files claiming one id collapse into a single graph node, so a
    deploy already stamped with that id plans nothing and the losing file's DDL is skipped while
    `upgrade heads` still reports success. Alembic detects the collision as a warning, which this
    raises."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        heads = ScriptDirectory.from_config(config).get_heads()
    assert {
        "index_default_0002",
        "memory_0007",
        "sample_ext_note_0001",
        "skill_create_0001",
        "knowledge_graph_0001",
        "eval_env_0001",
    } <= set(heads)
    assert len(heads) == 7


def test_memory_as_of_migration_repairs_page_derived_rows(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'memory-as-of.db'}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0048")
    command.upgrade(config, "memory_0006")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'memory-as-of.db'}")
    workspace_id, source_id, page_id, memory_id, manual_id = (uuid4() for _ in range(5))
    ingested = datetime(2026, 7, 24, tzinfo=UTC)
    source_as_of = datetime(2025, 7, 24, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :ingested, :ingested)"
            ),
            {"id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into source "
                "(id, workspace_id, backend, config, next_sync_at, created_at, updated_at) "
                "values (:id, :workspace_id, 'folder', '{}', :ingested, :ingested, :ingested)"
            ),
            {"id": source_id.hex, "workspace_id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into page "
                "(id, workspace_id, source_id, digest, body_ref, subject, tombstone, "
                "stream, title, "
                "source_created_at, source_updated_at, created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:page', 'pages/page', 'shared', false, "
                "'issues', 'Old issue', :source_as_of, null, :ingested, :ingested)"
            ),
            {
                "id": page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "source_as_of": source_as_of.isoformat(),
                "ingested": ingested,
            },
        )
        for item_id, source_ref in (
            (memory_id, str(page_id)),
            (manual_id, "member-authored"),
        ):
            connection.execute(
                sa.text(
                    "insert into memory_item "
                    "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                    "source_ref, created_at, updated_at) values "
                    "(:id, :workspace_id, 'shared', 'fact', 'fact', 'fact', 5, "
                    ":source_ref, :ingested, :ingested)"
                ),
                {
                    "id": item_id.hex,
                    "workspace_id": workspace_id.hex,
                    "source_ref": source_ref,
                    "ingested": ingested,
                },
            )
        connection.commit()

    command.upgrade(config, "memory_0007")
    with engine.connect() as connection:
        repaired = connection.execute(
            sa.text("select as_of from memory_item where id = :id"), {"id": memory_id.hex}
        ).scalar_one()
        untouched = connection.execute(
            sa.text("select as_of from memory_item where id = :id"), {"id": manual_id.hex}
        ).scalar_one()
    engine.dispose()

    assert datetime.fromisoformat(repaired).replace(tzinfo=UTC) == source_as_of
    assert untouched is None


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
