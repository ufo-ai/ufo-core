import asyncio
import os
import shutil
import socket
import sqlite3
import threading
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from ufo_testsupport.tables import reset_workspace_data

import ufo.db
from ufo import o11y
from ufo.db import (
    MIGRATIONS_DIR,
    WORKSPACE_GUC,
    _build_engine,
    _opened,
    apply_migrations,
    dispose_db,
    init_db,
    init_owner_db,
    owner_tx,
    verify_db_reachable,
    workspace_tx,
)
from ufo.ext.loader import migration_locations
from ufo.schema import tables
from ufo.workspace import ws


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
    """The migration seam's schema invariant: each extension's version location layers over
    core's — `apply_migrations` ran clean in the fixture — and the graph has exactly one head per
    owner (core's chain plus each extension branch), so `upgrade heads` is deterministic,
    core-first.

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
        "0077",
        "index_default_0002",
        "objectives_0001",
        "memory_0012",
        "sample_ext_note_0001",
        "skill_create_0002",
        "coding_0003",
        "eval_env_0001",
        "sites_0002",
        "web_0001",
    } <= set(heads)
    assert len(heads) == 11


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
    assert "0077" in revisions
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


def test_transcript_reads_names_disclosures_newest_first_and_pages_on_the_operator_s_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator's read of the disclosure record, which is the only read of it: `ufoctl
    transcript-reads` names who opened whose conversation and when, newest first, and the operator
    asking sets how far back the page reaches."""
    from click.testing import CliRunner

    from ufo.cli import TRANSCRIPT_READS_LIMIT, main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ufo.toml").write_text(
        '[database]\nurl = "sqlite+aiosqlite:///app.db"\n'
        '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
    )
    runner = CliRunner()
    assert runner.invoke(main, ["migrate"]).exit_code == 0

    database_path = tmp_path / "app.db"
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    admin_id, subject_id = uuid4(), uuid4()
    now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now.isoformat(), now.isoformat()),
        )
        for member_id, email, admin in (
            (admin_id, "boss@example.com", 1),
            (subject_id, "m@example.com", 0),
        ):
            connection.execute(
                "insert into member (id, workspace_id, email, is_admin, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (member_id.hex, workspace_id.hex, email, admin, now.isoformat(), now.isoformat()),
            )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now.isoformat(), now.isoformat()),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now.isoformat(), now.isoformat()),
        )
        for index in range(TRANSCRIPT_READS_LIMIT + 1):
            connection.execute(
                "insert into transcript_access (id, workspace_id, conversation_id, "
                "reader_member_id, subject_member_id, created_at) values (?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    workspace_id.hex,
                    conversation_id.hex,
                    admin_id.hex,
                    subject_id.hex,
                    (now + timedelta(minutes=index)).isoformat(),
                ),
            )

    listed = runner.invoke(main, ["transcript-reads"])
    assert listed.exit_code == 0
    assert "boss@example.com" in listed.output
    assert "m@example.com" in listed.output
    assert str(conversation_id) in listed.output
    assert len(listed.output.strip().splitlines()) == TRANSCRIPT_READS_LIMIT
    # Newest first, so the flood fills the default page and the earliest read falls off it.
    assert "2026-07-31 09:00" not in listed.output

    deeper = runner.invoke(main, ["transcript-reads", "--limit", str(TRANSCRIPT_READS_LIMIT + 1)])
    assert deeper.exit_code == 0
    assert "2026-07-31 09:00" in deeper.output

    assert runner.invoke(main, ["transcript-reads", "--limit", "0"]).exit_code != 0


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


REFUSED_DSN = "postgresql+asyncpg://ufo:ufo@127.0.0.1:1/ufo_test"


async def test_verify_db_reachable_fails_loud_on_a_database_it_cannot_reach() -> None:
    """The boot check the TCP-probed deployments rest on. A privileged port is used because nothing
    in this suite can bind it, so the refusal is deterministic — an ephemeral port is free for a
    parallel worker to take between the release and the connect."""
    init_db(REFUSED_DSN)
    try:
        with pytest.raises(ConnectionRefusedError):
            await verify_db_reachable()
    finally:
        await dispose_db()


async def test_verify_db_reachable_checks_the_owner_url_too(db: None) -> None:
    """`ufoctl proxy` and `ufoctl ingress` open one url, but `serve` opens two and reaches the
    database through the owner one — a check that only dialled the app url would pass a boot whose
    owner database is unreachable."""
    init_owner_db(REFUSED_DSN)
    with pytest.raises(ConnectionRefusedError):
        await verify_db_reachable()


async def test_verify_db_reachable_requires_init() -> None:
    with pytest.raises(RuntimeError, match="db not initialized"):
        await verify_db_reachable()


async def test_verify_db_reachable_publishes_no_engine(db: None) -> None:
    """It dials with its own engine and disposes it, so the check never leaves a pooled connection
    behind — a boot verb that seeded the registry would hand the first request an engine built on
    whatever loop happened to run the check."""
    before = (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines))
    await verify_db_reachable()
    assert (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines)) == before


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


async def test_a_refused_connect_counts_under_the_class_the_driver_really_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`path` is this counter's only other dimension, so `error_class` is its whole information
    content, and the emitter folds a class no allowlist entry names into `other` — which makes the
    exact name load-bearing. Nothing in the code names these: they are whatever the driver raises
    through the dialect, so both are taken from the real stack rather than guessed at as the
    `OperationalError` a SQLAlchemy-shaped guess would reach for.

    A pooled engine builds without dialing, so the dial is driven here: `connect()` on the engine
    this deployment builds runs the whole path — pool, dialect, driver — which answers what a bare
    driver connect cannot, whether the dialect re-wraps the driver's `OSError` on the way out. It
    does not, and a release that started to would fail here rather than send production to `other`.
    A pool is what makes this counter rare rather than impossible: a warm connection answers most
    transactions, and the ones that still dial — a loop's first touch, a recycled connection, the
    replacement `pool_pre_ping` opens for one the database dropped — are where the incident it
    reports lives. Both harvested classes then travel the rest of the way, through `_opened` to an
    exact attribute dict, so neither entry in the allowlist is held by a name mirrored between two
    lists.

    The refusal is taken from a privileged port, which nothing in this suite can bind: an ephemeral
    port picked by binding and releasing is free for any parallel worker to take between the release
    and the connect, and a worker that binds it as a server answers the TCP handshake and then never
    speaks Postgres, which hangs the read rather than refusing it. A port that cannot be bound at
    all has no such window."""

    async def dial(url: str) -> None:
        engine = _build_engine(url, ufo.db._APP)
        try:
            async with engine.connect():
                pass
        finally:
            await engine.dispose()

    with pytest.raises(ConnectionRefusedError) as refused:
        await dial("postgresql+asyncpg://ufo:ufo@127.0.0.1:1/ufo_test")
    with pytest.raises(socket.gaierror) as unresolved:
        await dial("postgresql+asyncpg://ufo:ufo@no-such-host.invalid:5432/ufo_test")

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    for harvested in (refused.value, unresolved.value):
        with pytest.raises(type(harvested)):
            async with _opened(_RefusingEngine(type(harvested)()), "workspace"):
                pass
    assert [
        dict(point.attributes)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.db_tx_unavailable_total"
        for point in metric.data.data_points
    ] == [
        {"path": "workspace", "error_class": "ConnectionRefusedError"},
        {"path": "workspace", "error_class": "gaierror"},
    ]


@pytest.fixture
def tx_engine(tmp_path: Path) -> AsyncEngine:
    url = f"sqlite+aiosqlite:///{tmp_path / 'transactions.db'}"
    apply_migrations(url)
    return _build_engine(url, ufo.db._APP)


async def _add_workspace(connection: AsyncConnection, workspace_id: UUID) -> None:
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )


async def _committed_workspaces(engine: AsyncEngine) -> list[UUID]:
    async with _opened(engine, "workspace") as connection:
        return list((await connection.execute(sa.select(tables.workspace.c.id))).scalars())


async def test_a_body_that_raises_commits_nothing(tx_engine: AsyncEngine) -> None:
    workspace_id = uuid4()

    with pytest.raises(ValueError):
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, workspace_id)
            raise ValueError("the caller's own failure")

    assert await _committed_workspaces(tx_engine) == []


async def test_a_cancel_inside_the_body_rolls_back_before_the_caller_sees_it(
    tx_engine: AsyncEngine,
) -> None:
    workspace_id = uuid4()
    written = asyncio.Event()
    opened: list[AsyncConnection] = []

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            opened.append(connection)
            await _add_workspace(connection, workspace_id)
            written.set()
            await asyncio.Event().wait()

    task = asyncio.ensure_future(step())
    await written.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert opened[0].closed
    assert await _committed_workspaces(tx_engine) == []


async def test_a_cancel_landing_inside_the_teardown_finishes_it_first(
    tx_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    opened: list[AsyncConnection] = []
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    original_commit = aiosqlite.Connection.commit
    pause_next_commit = True

    async def paused_commit(connection: aiosqlite.Connection) -> None:
        nonlocal pause_next_commit
        if pause_next_commit:
            pause_next_commit = False
            commit_started.set()
            await release_commit.wait()
        await original_commit(connection)

    monkeypatch.setattr(aiosqlite.Connection, "commit", paused_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            opened.append(connection)
            await _add_workspace(connection, workspace_id)

    task = asyncio.ensure_future(step())
    await commit_started.wait()
    task.cancel()
    release_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert asyncio.all_tasks() == {asyncio.current_task()}
    assert opened[0].closed
    assert await _committed_workspaces(tx_engine) == [workspace_id]


async def test_a_teardown_that_fails_raises_on_the_caller(tx_engine: AsyncEngine) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    with pytest.raises(RuntimeError, match="the commit lost the race"):
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())


async def test_a_teardown_that_fails_under_a_cancel_still_raises_on_the_caller(
    tx_engine: AsyncEngine,
) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        task.cancel()
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    with pytest.raises(RuntimeError, match="the commit lost the race"):
        await task


async def test_a_teardown_that_failed_before_its_cancel_landed_still_raises_on_the_caller(
    tx_engine: AsyncEngine,
) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        closing = asyncio.current_task()
        assert closing is not None
        closing.add_done_callback(lambda _: task.cancel())
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    with pytest.raises(RuntimeError, match="the commit lost the race"):
        await task


def test_the_unavailable_count_is_a_registered_metric() -> None:
    """`_opened` emits from inside its except block, so a name `o11y` does not know would raise
    `unknown metric` there and leave the caller holding that instead of the connect failure the
    count exists to report — the instrumentation destroying the error it was added to surface. The
    test above mocks the emit away to read its arguments, so this is what holds the name."""
    o11y.emit_metric("db_tx_unavailable_total", path="workspace", error_class="TimeoutError")
    o11y.emit_metric("db_pool_exhausted_total", path="workspace")
    o11y.emit_histogram("db_tx_acquire_ms", 1, path="workspace")


async def test_a_saturated_pool_is_counted_apart_from_a_lost_dial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two failures share a class name — SQLAlchemy raises its own `TimeoutError` when a
    checkout waits out `pool_timeout`, and a lost dial raises the builtin one — so `error_class`
    cannot tell an operator whether the fleet hit its ceiling or the network dropped a packet. The
    saturation count is what separates them, and it is emitted beside the unavailable count rather
    than instead of it: a transaction that never opened is still a transaction that never opened."""
    counted: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y, "emit_metric", lambda name, **dimensions: counted.append((name, dimensions))
    )

    with pytest.raises(sa.exc.TimeoutError):
        async with _opened(_RefusingEngine(sa.exc.TimeoutError("pool limit reached")), "workspace"):
            pass
    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "owner"):
            pass

    assert counted == [
        ("db_pool_exhausted_total", {"path": "workspace"}),
        ("db_tx_unavailable_total", {"path": "workspace", "error_class": "TimeoutError"}),
        ("db_tx_unavailable_total", {"path": "owner", "error_class": "TimeoutError"}),
    ]


async def test_the_acquire_wait_is_recorded_whether_or_not_the_transaction_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool that is filling shows up as a wait long before it shows up as a failure, and the wait
    that ends in a failure is the one worth the most — it is the queue at its longest, so the
    `finally` that records it is what the assertion below is really holding."""
    recorded: list[tuple[str, int, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y,
        "emit_histogram",
        lambda name, value, **dimensions: recorded.append((name, value, dimensions)),
    )
    monkeypatch.setattr(o11y, "emit_metric", lambda name, **dimensions: None)
    clock = iter((10.0, 10.25, 20.0, 20.5))
    monkeypatch.setattr(ufo.db, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    async with _opened(_RefusingEngine(None), "workspace"):
        pass
    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "owner"):
            pass

    assert recorded == [
        ("db_tx_acquire_ms", 250, {"path": "workspace"}),
        ("db_tx_acquire_ms", 500, {"path": "owner"}),
    ]


def _current_engine() -> AsyncEngine:
    loop = asyncio.get_running_loop()
    return next(engine for (held, _), engine in ufo.db._APP.engines.items() if held is loop)


async def _touch() -> None:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("select 1"))


async def _touch_and_dispose() -> None:
    await _touch()
    await _current_engine().dispose()


async def test_rls_guc_does_not_leak_across_a_reused_connection(
    db: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE pooling safety invariant: `set_config(..., true)` is `SET LOCAL`, so a pooled connection
    handed to the next checkout carries no residual workspace GUC. A single-connection pool forces
    physical reuse (equal backend pids prove it), and a non-superuser probe role — the test user is
    a superuser, which bypasses RLS — proves rows pinned under one workspace are invisible under
    another and that an unbound read still fails closed."""
    if not database_url.startswith("postgresql"):
        pytest.skip("RLS is a postgres mechanism")
    url = make_url(database_url)
    role = f"rls_probe_{url.database}"
    probe_url = url.set(username=role, password="rls-probe").render_as_string(hide_password=False)
    predicate = f"workspace_id = current_setting('{WORKSPACE_GUC}')::uuid"
    async with workspace_tx() as connection:
        await connection.execute(sa.text("create table rls_probe (workspace_id uuid not null)"))
        await connection.execute(sa.text("alter table rls_probe enable row level security"))
        await connection.execute(
            sa.text(
                f"create policy rls_probe_ws on rls_probe "
                f"using ({predicate}) with check ({predicate})"
            )
        )
        await connection.execute(sa.text(f'drop role if exists "{role}"'))
        await connection.execute(sa.text(f"create role \"{role}\" login password 'rls-probe'"))
        await connection.execute(sa.text(f'grant select, insert on rls_probe to "{role}"'))
    await dispose_db()
    monkeypatch.setattr(ufo.db, "_APP", replace(ufo.db._APP, size=1, overflow=0, engines={}))
    init_db(probe_url)
    try:
        workspace_a, workspace_b = uuid4(), uuid4()
        with ws(workspace_a):
            async with workspace_tx() as connection:
                pid_a = (await connection.execute(sa.text("select pg_backend_pid()"))).scalar_one()
                await connection.execute(
                    sa.text("insert into rls_probe values (cast(:ws as uuid))"),
                    {"ws": str(workspace_a)},
                )
        with ws(workspace_b):
            async with workspace_tx() as connection:
                pid_b = (await connection.execute(sa.text("select pg_backend_pid()"))).scalar_one()
                rows = (await connection.execute(sa.text("select * from rls_probe"))).all()
        assert pid_a == pid_b
        assert rows == []
        with pytest.raises(sa.exc.DBAPIError):
            async with workspace_tx() as connection:
                await connection.execute(sa.text("select * from rls_probe"))
    finally:
        await dispose_db()
        init_db(database_url)
        async with workspace_tx() as connection:
            await connection.execute(sa.text("drop table rls_probe"))
            await connection.execute(sa.text(f'drop role "{role}"'))
        await dispose_db()
        init_db(database_url)


async def test_same_loop_resolves_the_same_engine(db: None) -> None:
    await _touch()
    first = _current_engine()
    await _touch()
    assert _current_engine() is first


async def test_engines_are_per_loop(db: None) -> None:
    await _touch()
    main_engine = _current_engine()
    seen: list[AsyncEngine] = []

    async def capture() -> None:
        await _touch()
        seen.append(_current_engine())
        await _current_engine().dispose()

    thread = threading.Thread(target=lambda: asyncio.run(capture()))
    thread.start()
    thread.join()
    assert seen[0] is not main_engine


async def test_dead_loop_entries_are_pruned(db: None) -> None:
    thread = threading.Thread(target=lambda: asyncio.run(_touch_and_dispose()))
    thread.start()
    thread.join()
    assert any(held.is_closed() for held, _ in ufo.db._APP.engines)
    await _touch()
    assert not any(held.is_closed() for held, _ in ufo.db._APP.engines)


async def test_dispose_loop_engines_closes_the_connection_not_just_the_key(db: None) -> None:
    """What keeps a throwaway boot loop from abandoning a live connection. Dropping the registry key
    is not the property — a pooled connection whose loop then closes is a socket nothing in the
    process can ever close — so this holds the *close*: disposal replaces the engine's pool, and
    the one the escaped engine's connection was checked into is gone. Remove the
    `await engine.dispose()` from `dispose_loop_engines` and this fails, where a key-only assertion
    passes."""
    await _touch()
    main_engine = _current_engine()
    escaped: list[tuple[AsyncEngine, object]] = []

    async def one_shot() -> None:
        await _touch()
        engine = _current_engine()
        escaped.append((engine, engine.pool))
        await ufo.db.dispose_loop_engines()

    thread = threading.Thread(target=lambda: asyncio.run(one_shot()))
    thread.start()
    thread.join()
    engine, pool_before = escaped[0]
    assert engine.pool is not pool_before
    assert not any(held.is_closed() for held, _ in ufo.db._APP.engines)
    assert _current_engine() is main_engine


async def test_dispose_db_closes_a_live_foreign_loops_engine(db: None) -> None:
    """A loop still running when teardown starts is the shape this suite has — a session worker
    beside the test's own loop. Only that loop can close its connections, so they close *through* it
    rather than dropped (which would strand them) or left open (which contends with whatever runs
    next). The handoff is scheduled on that loop rather than awaited from this one, so the foreign
    engine's pool is read after its loop has drained `_disposing`; both pools are asserted replaced,
    which is what disposal does and what popping a key alone does not."""
    await _touch()
    ready, release = threading.Event(), threading.Event()
    escaped: list[tuple[AsyncEngine, object]] = []

    async def hold() -> None:
        await _touch()
        engine = _current_engine()
        escaped.append((engine, engine.pool))
        ready.set()
        await asyncio.to_thread(release.wait)
        await asyncio.gather(*list(ufo.db._disposing))

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=lambda: loop.run_until_complete(hold()))
    thread.start()
    try:
        ready.wait()
        mine = _current_engine()
        mine_pool = mine.pool
        await dispose_db()
        assert mine.pool is not mine_pool
        assert not ufo.db._APP.engines
        assert ufo.db._app_url is None
    finally:
        release.set()
        thread.join()
        loop.close()
    foreign, foreign_pool = escaped[0]
    assert foreign.pool is not foreign_pool


class _LoopThatClosesInTheWindow:
    """An event loop that reports itself open and then refuses the handoff — the one interleaving a
    check cannot exclude, since the loop can close between `is_closed()` returning False and
    `call_soon_threadsafe` being reached. Standing in for the loop, never for the assertion: what is
    asserted is what `dispose_db` does when a handoff is refused."""

    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, *_: object) -> None:
        raise RuntimeError("Event loop is closed")


async def test_a_loop_that_closes_mid_teardown_neither_raises_nor_holds_the_rest(
    db: None,
) -> None:
    """The failure has to stay inside teardown: seventeen callers are a bare
    `finally: await dispose_db()`, so a raise here replaces what drove the teardown, and an early
    exit would leave the pools after it untouched — the owner registry is iterated second, so its
    connections would stay open. The refused entry is dropped, because a loop that closed can no
    longer close anything."""
    await _touch()
    doomed = _LoopThatClosesInTheWindow()
    ufo.db._APP.engines[(doomed, "postgresql+asyncpg://doomed/app")] = _build_engine(
        "sqlite+aiosqlite:///doomed-app.db", ufo.db._APP
    )
    ufo.db._OWNER.engines[(doomed, "postgresql+asyncpg://doomed/owner")] = _build_engine(
        "sqlite+aiosqlite:///doomed-owner.db", ufo.db._OWNER
    )

    await dispose_db()

    assert not ufo.db._APP.engines
    assert not ufo.db._OWNER.engines
    assert ufo.db._app_url is None


async def test_dispose_db_clears_registries_and_reinit_works(db: None) -> None:
    """Re-initializes with the URL the fixture opened, not the `database_url` parameter: on sqlite
    that parameter is the session-scoped template the fixture handed this test a private copy of."""
    private = ufo.db._app_url
    assert private is not None
    await _touch()
    await dispose_db()
    assert not ufo.db._APP.engines
    assert not ufo.db._OWNER.engines
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass
    init_db(private)
    await _touch()


async def test_one_loop_holds_one_engine_per_url(db: None, database_url: str) -> None:
    """The url is half the registry key, and this is what fails if it leaves. Two urls on one loop
    have to resolve two engines: an entry a foreign loop left behind would otherwise be handed to a
    caller that re-initialized against a different database, which would read and write the previous
    one. Both dialects, since the key is not dialect-specific — the second url is the same database
    reached through the other driver on postgres, a second file on sqlite."""
    first = ufo.db._app_url
    assert first is not None
    if first.startswith("sqlite"):
        second = f"sqlite+aiosqlite:///{Path(make_url(first).database or '').parent / 'other.db'}"
        shutil.copy(make_url(first).database or "", make_url(second).database or "")
    else:
        second = (
            make_url(first)
            .set(drivername="postgresql+psycopg")
            .render_as_string(hide_password=False)
        )
    mine = ufo.db._engine_for(first, ufo.db._APP)
    other = ufo.db._engine_for(second, ufo.db._APP)
    try:
        assert other is not mine
        assert ufo.db._engine_for(first, ufo.db._APP) is mine
    finally:
        await other.dispose()
        ufo.db._APP.engines.pop((asyncio.get_running_loop(), second), None)


async def test_owner_tx_without_an_owner_url_resolves_the_app_engine(db: None) -> None:
    """`ufoctl ingress`, `ufoctl proxy`, and every one-shot verb open one URL, never an owner one.
    Building a second pool for that same URL would double their connection ceiling for nothing, so
    the fallback is the app pool's own engine — the identity, not a copy of the sizing."""
    await _touch()
    mine = _current_engine()
    async with owner_tx() as connection:
        await connection.execute(sa.text("select 1"))
    loop = asyncio.get_running_loop()
    assert not any(held is loop for held, _ in ufo.db._OWNER.engines)
    assert [key for key in ufo.db._APP.engines if key[0] is loop] == [(loop, ufo.db._app_url)]
    assert _current_engine() is mine


async def test_an_owner_url_gets_its_own_smaller_pool(db: None, database_url: str) -> None:
    """On the fleet `serve` sets both URLs, and then the owner pool is a second ceiling on the same
    instance. Its consumers are serial — the sweeps' enumeration and the heartbeat — so it is sized
    apart, and the budget in `db.py` counts it apart."""
    if not database_url.startswith("postgresql"):
        pytest.skip("sqlite holds no pool to size")
    assert ufo.db._app_url is not None
    init_owner_db(ufo.db._app_url)
    async with owner_tx() as connection:
        await connection.execute(sa.text("select 1"))
    owner_engine = next(iter(ufo.db._OWNER.engines.values()))
    await _touch()
    assert owner_engine is not _current_engine()
    assert owner_engine.pool.size() == ufo.db.OWNER_POOL_SIZE
    assert _current_engine().pool.size() == ufo.db.POOL_SIZE


async def test_pool_class_matches_dialect(db: None, database_url: str) -> None:
    await _touch()
    expected = "NullPool" if database_url.startswith("sqlite") else "AsyncAdaptedQueuePool"
    assert type(_current_engine().pool).__name__ == expected


def test_the_dial_is_bounded_and_the_pool_is_named() -> None:
    """asyncpg's default connect timeout is 60 seconds — longer than any turn will wait, and the
    whole of #834 — so the dial a replacement checkout drives is bounded next to the connect. The
    application name is what attributes a connection to its pool in `pg_stat_activity`, which is
    what the soak reads: without it both registries and DBOS's own pool arrive indistinguishable.
    Both spellings are asserted: a driver refuses the other's kwarg rather than ignoring it."""
    asyncpg = ufo.db._pool_kwargs("postgresql+asyncpg://ufo:ufo@localhost/ufo", ufo.db._OWNER)
    assert asyncpg["connect_args"] == {
        "timeout": ufo.db.CONNECT_TIMEOUT_SECONDS,
        "server_settings": {"application_name": "ufo_owner"},
        "prepared_statement_cache_size": 0,
    }
    assert asyncpg["pool_pre_ping"] is True
    assert asyncpg["pool_timeout"] == ufo.db.POOL_TIMEOUT_SECONDS

    psycopg = ufo.db._pool_kwargs("postgresql+psycopg://ufo:ufo@localhost/ufo", ufo.db._APP)
    assert psycopg["connect_args"] == {
        "connect_timeout": ufo.db.CONNECT_TIMEOUT_SECONDS,
        "application_name": "ufo_app",
        "prepare_threshold": None,
    }


async def test_every_driver_a_composition_root_opens_can_actually_connect(
    database_url: str,
) -> None:
    """`ufoctl proxy` and `ufoctl ingress` open a psycopg DSN (`proxy_serve.owner_dsn` rewrites
    the scheme), and `serve` opens asyncpg. A connect kwarg is per-driver, and the
    wrong one is refused rather than ignored — psycopg rejects asyncpg's `timeout` as an unknown
    connection option — so asserting the kwarg dict alone would have left both those deployments
    unable to open a single connection. This drives a real connect through each driver instead."""
    if not database_url.startswith("postgresql"):
        pytest.skip("one postgres instance, two drivers")
    psycopg_url = make_url(database_url).set(drivername="postgresql+psycopg")
    for url in (database_url, psycopg_url.render_as_string(hide_password=False)):
        engine = _build_engine(url, ufo.db._APP)
        try:
            async with engine.connect() as connection:
                named = await connection.execute(
                    sa.text("select current_setting('application_name')")
                )
                assert named.scalar_one() == "ufo_app"
        finally:
            await engine.dispose()


async def test_a_warm_connection_survives_ddl_from_another_process(
    db: None, database_url: str
) -> None:
    """The regression pooling made reachable, driven end to end rather than asserted as a kwarg.
    asyncpg caches 100 prepared statements per connection and a pooled connection carries them for
    `pool_recycle`, while the `ufo-migrate` Job runs alembic against a live fleet — so a plan whose
    table changed underneath is `InvalidCachedStatementError` on the next execute. The DDL lands
    on a second connection, exactly as it does from outside the process, and the warm connection
    has to keep working.

    Two details are load-bearing. `select *` is what `add column` changes the result descriptor of,
    which is what a cached plan goes stale against — a select naming its columns survives the DDL
    with the cache at its default, so it would prove nothing. And the rollback is what lets the DDL
    take its lock: a `connect()` that has executed is inside a transaction until told otherwise, and
    asyncpg's plan cache is per DBAPI connection, so releasing the transaction does not release the
    cache this is about. The DDL connection takes a `lock_timeout` so a rollback that stops
    happening fails this test instead of wedging on the warm connection's lock."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prepared statements are a postgres mechanism")
    engine = _build_engine(database_url, ufo.db._APP)
    table = "plan_cache_probe"
    try:
        async with engine.connect() as setup:
            await setup.execute(sa.text(f"create table {table} (id int)"))
            await setup.commit()
        async with engine.connect() as warm:
            for _ in range(8):
                await warm.execute(sa.text(f"select * from {table}"))
            await warm.rollback()
            async with engine.connect() as elsewhere:
                await elsewhere.execute(sa.text("set lock_timeout = '5s'"))
                await elsewhere.execute(sa.text(f"alter table {table} add column added int"))
                await elsewhere.commit()
            await warm.execute(sa.text(f"select * from {table}"))
        async with engine.connect() as teardown:
            await teardown.execute(sa.text(f"drop table {table}"))
            await teardown.commit()
    finally:
        await engine.dispose()


async def test_a_committed_psycopg_connection_survives_ddl_from_another_process(
    db: None, database_url: str
) -> None:
    """The psycopg half, which needs a committed transaction to show at all: psycopg discards its
    plan cache on rollback, so a rollback-shaped probe proves nothing here — and commit is the path
    `ufoctl proxy` and `ufoctl ingress` run. Eight committed selects, DDL from another connection,
    then the same statement: with the cache at its default this raises `FeatureNotSupported: cached
    plan must not change result type`."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prepared statements are a postgres mechanism")
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = _build_engine(url.render_as_string(hide_password=False), ufo.db._APP)
    table = "psycopg_plan_cache_probe"
    try:
        async with engine.connect() as setup:
            await setup.execute(sa.text(f"create table {table} (id int)"))
            await setup.commit()
        async with engine.connect() as warm:
            for _ in range(8):
                await warm.execute(sa.text(f"select * from {table}"))
                await warm.commit()
            async with engine.connect() as elsewhere:
                await elsewhere.execute(sa.text("set lock_timeout = '5s'"))
                await elsewhere.execute(sa.text(f"alter table {table} add column added int"))
                await elsewhere.commit()
            await warm.execute(sa.text(f"select * from {table}"))
        async with engine.connect() as teardown:
            await teardown.execute(sa.text(f"drop table {table}"))
            await teardown.commit()
    finally:
        await engine.dispose()


async def test_dispose_loop_engines_closes_the_owner_pool_too(db: None) -> None:
    """Both of `_one_shot`'s production callers reach the database through `owner_tx`, so the
    engine a throwaway boot loop abandons is the owner one — skipping that registry is the leak
    this function exists to prevent, and the app pool's proof cannot see it.

    The owner url is the one the fixture opened, never the `database_url` parameter: on sqlite that
    parameter is the session template every later test copies, and binding a writer to it takes the
    one writer slot the fixture's private copy exists to keep separate."""
    private = ufo.db._app_url
    assert private is not None
    init_owner_db(private)
    escaped: list[tuple[AsyncEngine, object]] = []

    async def one_shot() -> None:
        async with owner_tx() as connection:
            await connection.execute(sa.text("select 1"))
        loop = asyncio.get_running_loop()
        engine = next(built for (held, _), built in ufo.db._OWNER.engines.items() if held is loop)
        escaped.append((engine, engine.pool))
        await ufo.db.dispose_loop_engines()

    thread = threading.Thread(target=lambda: asyncio.run(one_shot()))
    thread.start()
    thread.join()
    engine, pool_before = escaped[0]
    assert engine.pool is not pool_before
    assert not ufo.db._OWNER.engines


async def test_a_pool_at_its_ceiling_raises_the_class_the_saturation_count_branches_on(
    db: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The premise the saturation mechanism rests on, taken from SQLAlchemy rather than from a
    fake: `db_pool_exhausted_total` is emitted only where `_opened` sees SQLAlchemy's own
    `TimeoutError`, so a real pool at its ceiling raising anything else leaves the metric silent and
    the operator reading the fleet's ceiling as a network fault. A one-connection pool with checkout
    wait driven to zero reaches that ceiling on the second concurrent transaction."""
    if not database_url.startswith("postgresql"):
        pytest.skip("sqlite holds no pool to exhaust")
    counted: list[str] = []
    monkeypatch.setattr(o11y, "emit_metric", lambda name, **dimensions: counted.append(name))
    monkeypatch.setattr(ufo.db, "POOL_TIMEOUT_SECONDS", 0)
    pinned = replace(ufo.db._APP, size=1, overflow=0, engines={})
    monkeypatch.setattr(ufo.db, "_APP", pinned)
    try:
        async with workspace_tx():
            with pytest.raises(sa.exc.TimeoutError):
                async with workspace_tx():
                    pass
    finally:
        for engine in pinned.engines.values():
            await engine.dispose()

    assert counted == ["db_pool_exhausted_total", "db_tx_unavailable_total"]


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


def test_a_migrated_sqlite_file_needs_no_journal_conversion_from_its_readers(
    tmp_path: Path,
) -> None:
    """A migrated sqlite file is left in the mode its engines already use, and holds all its rows.

    Converting the journal mode takes an exclusive lock it cannot wait out: a connection that has to
    convert while another holds the file ends in `database is locked`. Leaving the migrated file
    converted means the engine every caller builds never asks for that lock — proved here by
    building one against a byte copy the way the test fixture copies its template, while a write is
    held open on it."""
    template = tmp_path / "template.db"
    apply_migrations(f"sqlite+aiosqlite:///{template}")
    assert not template.with_name(f"{template.name}-wal").exists()

    copy = tmp_path / "copy.db"
    shutil.copy(template, copy)
    with sqlite3.connect(copy) as seeded:
        assert seeded.execute("select count(*) from alembic_version").fetchone()[0] > 0

    holder = sqlite3.connect(copy, timeout=0.2)
    holder.execute("begin immediate")
    holder.execute(
        "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
        (str(uuid4()), "2026-01-01", "2026-01-01"),
    )
    try:
        _build_engine(f"sqlite+aiosqlite:///{copy}", ufo.db._APP)
    finally:
        holder.rollback()
        holder.close()


def test_a_libpq_owner_dsn_registers_as_the_async_driver() -> None:
    """The owner DSN arrives from a secret store in libpq form. SQLAlchemy resolves that to the sync
    psycopg2 dialect — a banned import that is not installed — so an engine built from it raises
    before any query runs, and every `owner_tx` caller fails at its first read. Normalizing where
    the URL is registered is what keeps a caller that passes the secret through verbatim working.
    """
    ufo.db._owner_url = None
    try:
        init_owner_db("postgresql://ufo_owner:secret@db.internal:5432/ufo")
        assert ufo.db._owner_url == "postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo"
    finally:
        ufo.db._owner_url = None


def test_an_owner_dsn_that_already_names_its_driver_is_untouched() -> None:
    ufo.db._owner_url = None
    try:
        init_owner_db("postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo")
        assert ufo.db._owner_url == "postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo"
    finally:
        ufo.db._owner_url = None
