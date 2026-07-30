import os
import sqlite3
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from ufo_testsupport.tables import reset_workspace_data

from ufo import o11y
from ufo.db import MIGRATIONS_DIR, _opened, apply_migrations, workspace_tx
from ufo.ext.loader import migration_locations
from ufo.schema import tables


@dataclass
class _RefusingEngine:
    """An engine whose `begin()` refuses to open, standing in for a Postgres that never answers the
    connect. The real engine cannot be made to fail this way without a database to take away."""

    error: Exception | None

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[object]:
        if self.error is not None:
            raise self.error
        yield object()


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


async def test_reset_wipes_every_application_table_and_keeps_the_stamp(
    db: None, database_url: str
) -> None:
    """`reset_workspace_data`'s own contract, asserted directly: a core row, an extension row, and
    an index chunk all vanish; the alembic stamp survives; and on sqlite the FTS5 virtual table is
    emptied through its own surface, leaving the index writable afterwards."""
    workspace_id = uuid4()
    sqlite = database_url.startswith("sqlite")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.text(
                "insert into memory_item "
                "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                "created_at, updated_at) values "
                "(:id, :workspace_id, 'shared', 'probe', 'fact', 'fact', 5, :now, :now)"
            ),
            {"id": uuid4().hex, "workspace_id": workspace_id.hex, "now": datetime.now(UTC)},
        )
        if sqlite:
            await connection.execute(
                sa.text(
                    "insert into chunk (chunk_digest, owner_kind, owner_id, subject, ordinal, "
                    "text) values ('sha256:probe', 'memory', :owner, 'shared', 0, 'probe row')"
                ),
                {"owner": uuid4().hex},
            )
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (chunk_digest, text) "
                    "values ('sha256:probe', 'probe row')"
                )
            )
        else:
            await connection.execute(
                sa.text(
                    "insert into chunk (workspace_id, chunk_digest, owner_kind, owner_id, "
                    "subject, ordinal, text) values (:workspace_id, 'sha256:probe', 'memory', "
                    ":owner, 'shared', 0, 'probe row')"
                ),
                {"workspace_id": str(workspace_id), "owner": str(uuid4())},
            )

    async with workspace_tx() as connection:
        await reset_workspace_data(connection)

    async with workspace_tx() as connection:
        for table in ("workspace", "memory_item", "chunk"):
            count = (
                await connection.execute(sa.text(f"select count(*) from {table}"))
            ).scalar_one()
            assert count == 0, table
        stamped = (
            await connection.execute(sa.text("select count(*) from alembic_version"))
        ).scalar_one()
        assert stamped >= 1
        if sqlite:
            fts = (await connection.execute(sa.text("select count(*) from chunk_fts"))).scalar_one()
            assert fts == 0
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (chunk_digest, text) "
                    "values ('sha256:after', 'still writable')"
                )
            )
            matched = (
                await connection.execute(
                    sa.text("select count(*) from chunk_fts where chunk_fts match 'writable'")
                )
            ).scalar_one()
            assert matched == 1


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


def test_apply_migrations_rejects_duplicate_revision_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two files claiming one revision id are one graph node, so the losing DDL would be skipped."""
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
    eval_env owns the fake mailbox and calendar tables, and sites owns the hosted_site table.

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
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
    assert scripts.get_revision("memory_0008").dependencies == "0049"
    assert {
        "0062",
        "index_default_0002",
        "memory_0012",
        "sample_ext_note_0001",
        "skill_create_0002",
        "eval_env_0001",
        "sites_0001",
    } <= set(heads)
    assert len(heads) == 7


@pytest.mark.parametrize("graph_installed", [False, True])
def test_one_memory_surface_advances_both_old_heads(tmp_path: Path, graph_installed: bool) -> None:
    database_path = tmp_path / f"graph-revision-{graph_installed}.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0051")
    if graph_installed:
        command.upgrade(config, "knowledge_graph_0001")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
    if graph_installed:
        assert {"graph_entity", "graph_edge"} <= tables
    else:
        assert not {"graph_entity", "graph_edge"} & tables

    apply_migrations(url)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
        revisions = {
            row[0] for row in connection.execute("select version_num from alembic_version")
        }
    assert not {"graph_entity", "graph_edge"} & tables
    assert "0062" in revisions
    assert "knowledge_graph_0001" not in revisions


def test_shared_artifact_id_backfills_every_existing_row(tmp_path: Path) -> None:
    """0061 gives already-shared files their row identity: the column arrives non-null and unique,
    so files a deploy shared before the paging cursor existed page by it afterwards — two files one
    turn shared in the same instant included, which is the tie the cursor cannot break without
    this column."""
    database_path = tmp_path / "artifact-id.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0060")
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 29, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
            "inbound, created_at, updated_at) values (?, ?, ?, ?, 1, 'queued', '{}', ?, ?)",
            (turn_id.hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
        )
        for name in ("chart.png", "notes.txt"):
            connection.execute(
                "insert into shared_artifact (turn_id, blob_key, workspace_id, filename, "
                "media_type, size_bytes, created_at, updated_at) "
                "values (?, ?, ?, ?, 'text/plain', 3, ?, ?)",
                (turn_id.hex, f"artifacts/x/{name}", workspace_id.hex, name, now, now),
            )
    command.upgrade(config, "0061")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("select filename, id from shared_artifact").fetchall()
    identities = {name: value for name, value in rows}
    assert set(identities) == {"chart.png", "notes.txt"}
    assert all(value for value in identities.values())
    assert len(set(identities.values())) == 2


def test_intent_admission_downgrade_rewrites_to_internal(tmp_path: Path) -> None:
    """0060's downgrade parks intent turns on the inert source: 'internal' matches no member-turn
    projection and no seat gate, so a machine envelope never renders as a member's message."""
    database_path = tmp_path / "intent-downgrade.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0060")
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 29, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'intent/a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
            "inbound, admission_source, created_at, updated_at) "
            "values (?, ?, ?, ?, 1, 'queued', '{}', 'intent', ?, ?)",
            (turn_id.hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
        )
    command.downgrade(config, "0058")
    with sqlite3.connect(database_path) as connection:
        source = connection.execute(
            "select admission_source from turn where id = ?", (turn_id.hex,)
        ).fetchone()[0]
        refused = False
        try:
            connection.execute(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, admission_source, created_at, updated_at) "
                "values (?, ?, ?, ?, 2, 'queued', '{}', 'intent', ?, ?)",
                (uuid4().hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
            )
        except sqlite3.IntegrityError:
            refused = True
    assert source == "internal"
    assert refused


def test_memory_as_of_migration_repairs_page_derived_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "memory-as-of.db"
    url = f"sqlite+aiosqlite:///{database_path}"
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
    command.upgrade(config, "memory_0007")

    engine = sa.create_engine(f"sqlite:///{database_path}")
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
                "stream, title, source_created_at, source_updated_at, "
                "created_at, updated_at) values "
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

    command.upgrade(config, "0049")
    command.upgrade(config, "memory_0008")
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


def test_memory_provenance_migration_backfills_page_derived_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "memory-provenance.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0050")
    command.upgrade(config, "memory_0008")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, source_id, page_id = (uuid4() for _ in range(3))
    derived_id, manual_id, dangling_id = (uuid4() for _ in range(3))
    ingested = datetime(2026, 7, 24, tzinfo=UTC)
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
                "stream, title, created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:page', 'pages/page', 'shared', false, "
                "'issues', 'Old issue', :ingested, :ingested)"
            ),
            {
                "id": page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "ingested": ingested,
            },
        )
        for item_id, source_ref in (
            (derived_id, str(page_id)),
            (manual_id, "member-authored"),
            (dangling_id, str(uuid4())),
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

    command.upgrade(config, "memory_0009")
    with engine.connect() as connection:
        derived, manual, dangling = (
            connection.execute(
                sa.text("select created_from_page_id, source_ref from memory_item where id = :id"),
                {"id": item_id.hex},
            ).one()
            for item_id in (derived_id, manual_id, dangling_id)
        )
    engine.dispose()

    assert derived.created_from_page_id == page_id.hex
    assert derived.source_ref is None
    assert manual.created_from_page_id is None and manual.source_ref == "member-authored"
    assert dangling.created_from_page_id is None and dangling.source_ref is not None


def test_agent_binding_migration_backfills_the_earliest_agent(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-bindings.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0050")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, later_agent, earliest_agent, conversation_id = (uuid4() for _ in range(4))
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": workspace_id.hex, "moment": datetime(2026, 7, 1, tzinfo=UTC)},
        )
        for agent_id, name, created in (
            (later_agent, "exec", datetime(2026, 7, 5, tzinfo=UTC)),
            (earliest_agent, "assistant", datetime(2026, 7, 2, tzinfo=UTC)),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace_id, :name, 'p', 'eval', true, :created, :created)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace_id": workspace_id.hex,
                    "name": name,
                    "created": created,
                },
            )
        connection.execute(
            sa.text(
                "insert into surface_installation "
                "(workspace_id, surface, installation_id, created_at, updated_at) "
                "values (:workspace_id, 'slack', 'team:T1', :moment, :moment)"
            ),
            {"workspace_id": workspace_id.hex, "moment": datetime(2026, 7, 3, tzinfo=UTC)},
        )
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, surface, queue_key, created_at, updated_at) "
                "values (:id, :workspace_id, 'cli', 'session', :moment, :moment)"
            ),
            {
                "id": conversation_id.hex,
                "workspace_id": workspace_id.hex,
                "moment": datetime(2026, 7, 3, tzinfo=UTC),
            },
        )
        connection.commit()

    command.upgrade(config, "0051")
    with engine.connect() as connection:
        installation_agent = connection.execute(
            sa.text("select agent_id from surface_installation where workspace_id = :id"),
            {"id": workspace_id.hex},
        ).scalar_one()
        conversation_agent = connection.execute(
            sa.text("select agent_id from conversation where id = :id"),
            {"id": conversation_id.hex},
        ).scalar_one()
    engine.dispose()

    assert installation_agent == earliest_agent.hex
    assert conversation_agent == earliest_agent.hex


def test_user_skill_migration_backfills_within_each_workspace_and_enforces_agent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent-skills.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0051")
    command.upgrade(config, "skill_create_0001")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    first_workspace, second_workspace = uuid4(), uuid4()
    first_earliest, first_later, second_earliest, second_later = (uuid4() for _ in range(4))
    moment = datetime(2026, 7, 1, tzinfo=UTC)
    with engine.connect() as connection:
        for workspace_id in (first_workspace, second_workspace):
            connection.execute(
                sa.text(
                    "insert into workspace (id, created_at, updated_at) "
                    "values (:id, :moment, :moment)"
                ),
                {"id": workspace_id.hex, "moment": moment},
            )
        for agent_id, workspace_id, name, created in (
            (first_later, first_workspace, "first-later", datetime(2026, 7, 5, tzinfo=UTC)),
            (
                first_earliest,
                first_workspace,
                "first-earliest",
                datetime(2026, 7, 2, tzinfo=UTC),
            ),
            (
                second_later,
                second_workspace,
                "second-later",
                datetime(2026, 7, 3, tzinfo=UTC),
            ),
            (
                second_earliest,
                second_workspace,
                "second-earliest",
                datetime(2026, 7, 1, tzinfo=UTC),
            ),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace_id, :name, 'p', 'eval', true, :created, :created)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace_id": workspace_id.hex,
                    "name": name,
                    "created": created,
                },
            )
        for workspace_id in (first_workspace, second_workspace):
            connection.execute(
                sa.text(
                    "insert into user_skill "
                    "(workspace_id, name, digest, content, created_at, updated_at) "
                    "values (:workspace_id, 'greet', 'sha256:old', "
                    "'{\"files\": {}}', :moment, :moment)"
                ),
                {"workspace_id": workspace_id.hex, "moment": moment},
            )
        connection.commit()

    command.upgrade(config, "skill_create_0002")
    with engine.connect() as connection:
        connection.exec_driver_sql("pragma foreign_keys = on")
        assert connection.exec_driver_sql("pragma foreign_keys").scalar_one() == 1
        rows = connection.execute(
            sa.text("select workspace_id, agent_id from user_skill order by workspace_id")
        ).all()
        foreign_keys = sa.inspect(connection).get_foreign_keys("user_skill")
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "insert into user_skill "
                    "(workspace_id, agent_id, name, digest, content, created_at, updated_at) "
                    "values (:workspace_id, :agent_id, 'orphan', 'sha256:orphan', "
                    "'{\"files\": {}}', :moment, :moment)"
                ),
                {
                    "workspace_id": first_workspace.hex,
                    "agent_id": uuid4().hex,
                    "moment": moment,
                },
            )
            connection.commit()
    engine.dispose()

    assert {(row.workspace_id, row.agent_id) for row in rows} == {
        (first_workspace.hex, first_earliest.hex),
        (second_workspace.hex, second_earliest.hex),
    }
    assert any(
        foreign_key["name"] == "user_skill_agent_id_fkey"
        and foreign_key["referred_table"] == "agent"
        and foreign_key["constrained_columns"] == ["agent_id"]
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in foreign_keys
    )


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


async def test_conversation_and_memory_audiences_are_constrained(db: None) -> None:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    memory_item = sa.table(
        "memory_item",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("subject", sa.Text()),
        sa.column("body", sa.Text()),
        sa.column("item_class", sa.Text()),
        sa.column("memory_kind", sa.Text()),
        sa.column("confidence", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
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
        invalid = (
            ("invalid", None),
            (f"member:{member_id}", None),
            ("shared", member_id),
        )
        for index, (audience, row_member_id) in enumerate(invalid):
            with pytest.raises(sa.exc.IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        sa.insert(tables.conversation).values(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            agent_id=agent_id,
                            surface="test",
                            queue_key=f"invalid-{index}",
                            member_id=row_member_id,
                            audience=audience,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                    )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(memory_item).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        subject="invalid",
                        body="probe",
                        item_class="fact",
                        memory_kind="fact",
                        confidence=5,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )


async def test_workspace_tx_requires_init() -> None:
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass


async def test_a_transaction_that_never_opens_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect that never lands is invisible to the database — it never receives it — so this
    count is the only place the failure exists. It has to be the acquisition alone: counting the
    whole block would report every failing query as a database that could not be reached."""
    counted: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y, "emit_metric", lambda name, **dimensions: counted.append((name, dimensions))
    )

    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "workspace"):
            pass
    async with _opened(_RefusingEngine(None), "workspace"):
        pass
    with pytest.raises(ValueError):
        async with _opened(_RefusingEngine(None), "workspace"):
            raise ValueError("the caller's own failure")

    assert counted == [
        ("db_tx_unavailable_total", {"path": "workspace", "error_class": "TimeoutError"})
    ]


def test_the_unavailable_count_is_a_registered_metric() -> None:
    """`_opened` emits from inside its except block, so a name `o11y` does not know would raise
    `unknown metric` there and leave the caller holding that instead of the connect failure the
    count exists to report — the instrumentation destroying the error it was added to surface. The
    test above mocks the emit away to read its arguments, so this is what holds the name."""
    o11y.emit_metric("db_tx_unavailable_total", path="workspace", error_class="TimeoutError")


async def test_agent_reasoning_is_constrained(db: None) -> None:
    """The column is the last gate on the effort a turn runs at: a value outside the enum is
    refused by the database, so a writer that bypasses the spec cannot seat `turbo` in a row the
    engine then hands the provider."""
    async with workspace_tx() as connection:
        workspace_id = uuid4()
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name="assistant",
                        prompt="p",
                        model="m",
                        reasoning="turbo",
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        seated = await connection.execute(
            sa.insert(tables.agent)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                name="defaulted",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .returning(tables.agent.c.reasoning)
        )
        assert seated.scalar_one() == "auto"


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
                agent_id=agent_id,
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
