import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.ext.loader import migration_locations


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "page-revision.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_page_revision_{uuid4().hex[:8]}"
    admin = sa.create_engine(
        source.set(drivername="postgresql+psycopg", database="ufo"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f'create database "{database}"')
    try:
        yield (
            source.set(database=database).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=database).render_as_string(
                hide_password=False
            ),
        )
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'drop database "{database}"')
        admin.dispose()


def test_page_revision_migration_invalidates_derivations_and_requests_full_replay(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url)
    command.upgrade(config, "0053")
    command.upgrade(config, "memory_0009")

    engine = sa.create_engine(sync_url)
    workspace_id, source_id, page_id, later_page_id, derived_id, manual_id = (
        uuid4() for _ in range(6)
    )
    now = datetime(2026, 7, 27, tzinfo=UTC)
    later = datetime(2026, 7, 28, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into source "
                "(id, workspace_id, backend, config, next_sync_at, created_at, updated_at) "
                "values (:id, :workspace_id, 'folder', '{}', :now, :now, :now)"
            ),
            {"id": source_id.hex, "workspace_id": workspace_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into page "
                "(id, workspace_id, source_id, digest, body_ref, stream, title, subject, "
                "tombstone, created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:page', 'pages/page', 'notes', "
                "'Page', 'shared', false, :now, :now)"
            ),
            {
                "id": page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "insert into page "
                "(id, workspace_id, source_id, digest, body_ref, stream, title, subject, "
                "tombstone, created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:later', 'pages/later', 'notes', "
                "'Later', 'shared', false, :later, :later)"
            ),
            {
                "id": later_page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "later": later,
            },
        )
        for item_id, page_origin in ((derived_id, page_id.hex), (manual_id, None)):
            connection.execute(
                sa.text(
                    "insert into memory_item "
                    "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                    "created_from_page_id, embedding_digest, embedding_claimed_at, "
                    "created_at, updated_at) values "
                    "(:id, :workspace_id, 'shared', :body, 'fact', 'fact', 5, "
                    ":page_origin, 'sha256:embedding', :now, :now, :now)"
                ),
                {
                    "id": item_id.hex,
                    "workspace_id": workspace_id.hex,
                    "body": f"fact {item_id}",
                    "page_origin": page_origin,
                    "now": now,
                },
            )
        connection.execute(
            sa.text(
                "insert into mem_page (page_id, workspace_id, subject, created_at) "
                "values (:page_id, :workspace_id, 'shared', :now)"
            ),
            {"page_id": page_id.hex, "workspace_id": workspace_id.hex, "now": now},
        )
        for extension, key, value in (
            ("memory", "page_change_cursor:index_pages", f"{now.isoformat()}|{page_id}"),
            ("memory", "page_change_cursor:derive_facts", f"{now.isoformat()}|{page_id}"),
            ("memory", "unrelated", "value"),
            ("sample", "page_change_cursor:index_pages", f"{now.isoformat()}|{page_id}"),
            ("sources", "page_change_cursor:notify", f"{now.isoformat()}|{page_id}"),
            ("sample", "pageXchangeYcursor:opaque", "untouched"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:workspace_id, :extension, :key, :value, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id.hex,
                    "extension": extension,
                    "key": key,
                    "value": f'"{value}"',
                    "now": now,
                },
            )
        connection.commit()

    command.upgrade(config, "0054")
    command.upgrade(config, "memory_0010")
    with engine.connect() as connection:
        derived = connection.execute(
            sa.text(
                "select created_from_page_revision, embedding_digest, embedding_claimed_at "
                "from memory_item where id = :id"
            ),
            {"id": derived_id.hex},
        ).one()
        manual = connection.execute(
            sa.text(
                "select embedding_digest, embedding_claimed_at from memory_item where id = :id"
            ),
            {"id": manual_id.hex},
        ).one()
        mirrors = connection.execute(sa.text("select count(*) from mem_page")).scalar_one()
        page_revisions = {
            UUID(str(stored_id)): revision
            for stored_id, revision in connection.execute(
                sa.text("select id, revision from page order by revision")
            ).tuples()
        }
        cursors = {
            key: json.loads(value) if value.startswith('"') else value
            for key, value in connection.execute(
                sa.text("select extension || ':' || key, value from ext_store")
            ).tuples()
        }
    engine.dispose()

    assert derived == (None, None, None)
    assert manual.embedding_digest == "sha256:embedding"
    assert datetime.fromisoformat(str(manual.embedding_claimed_at)).replace(tzinfo=UTC) == now
    assert mirrors == 0
    assert page_revisions == {page_id: 1, later_page_id: 2}
    translated = f"1|{page_id}"
    assert cursors == {
        "memory:unrelated": "value",
        "sample:page_change_cursor:index_pages": translated,
        "sources:page_change_cursor:notify": translated,
        "sample:pageXchangeYcursor:opaque": "untouched",
    }

    command.downgrade(config, "memory_0009")
    command.downgrade(config, "0053")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        remaining = {
            key: json.loads(value) if value.startswith('"') else value
            for key, value in connection.execute(
                sa.text("select extension || ':' || key, value from ext_store")
            ).tuples()
        }
    engine.dispose()
    assert remaining == {
        "memory:unrelated": "value",
        "sample:pageXchangeYcursor:opaque": "untouched",
    }
