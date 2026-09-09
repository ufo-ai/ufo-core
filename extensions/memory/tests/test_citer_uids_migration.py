import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

CORE_HEAD = "20260909062725"
BEFORE = "memory_0017"
REVISION = "memory_0018"
NOW = "2026-09-09 06:30:00+00:00"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_every_citer_takes_its_parents_uid(tmp_path: Path) -> None:
    """A link, a mirror and a memory's primary binding each name a page or a source by content id;
    the twin column is filled from the very parent that id names, so the two identities agree row
    by row. A memory with no page keeps both twins null, as its ids are."""
    database = tmp_path / "citer-uids.db"
    config = _config(database)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, BEFORE)

    workspace_id, connection_id = uuid4(), uuid4()
    source_id, source_uid, page_id, page_uid = uuid4(), uuid4(), uuid4(), uuid4()
    derived_id, manual_id = uuid4(), uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into connection (id, workspace_id, provider, account_id, host, shared, "
                "created_at, updated_at) values (:id, :ws, 'folder', '', '', 1, :now, :now)"
            ),
            {"id": connection_id.hex, "ws": workspace_id.hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
                "connection_id, next_sync_at, created_at, updated_at) values "
                "(:id, :uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
            ),
            {
                "id": source_id.hex,
                "uid": source_uid.hex,
                "ws": workspace_id.hex,
                "conn": connection_id.hex,
                "now": NOW,
            },
        )
        connection.execute(
            sa.text(
                "insert into page (id, uid, workspace_id, source_id, digest, body_ref, stream, "
                "title, subject, tombstone, created_at, updated_at) values "
                "(:id, :uid, :ws, :src, 'sha256:p', 'pages/p', 'notes', 'Page', 'shared', 0, "
                ":now, :now)"
            ),
            {
                "id": page_id.hex,
                "uid": page_uid.hex,
                "ws": workspace_id.hex,
                "src": source_id.hex,
                "now": NOW,
            },
        )
        for item_id, page, source in (
            (derived_id, page_id.hex, source_id.hex),
            (manual_id, None, None),
        ):
            connection.execute(
                sa.text(
                    "insert into memory_item (id, workspace_id, subject, body, item_class, "
                    "memory_kind, confidence, created_from_page_id, created_from_page_revision, "
                    "source_id, created_at, updated_at) values (:id, :ws, 'shared', :body, 'fact', "
                    "'fact', 5, :page, :rev, :src, :now, :now)"
                ),
                {
                    "id": item_id.hex,
                    "ws": workspace_id.hex,
                    "body": f"fact {item_id}",
                    "page": page,
                    "rev": 1 if page else None,
                    "src": source,
                    "now": NOW,
                },
            )
        connection.execute(
            sa.text(
                "insert into memory_source (workspace_id, memory_item_id, source_id, page_id, "
                "revision, created_at, updated_at) values (:ws, :item, :src, :page, 1, :now, :now)"
            ),
            {
                "ws": workspace_id.hex,
                "item": derived_id.hex,
                "src": source_id.hex,
                "page": page_id.hex,
                "now": NOW,
            },
        )
        connection.execute(
            sa.text(
                "insert into mem_page (page_id, workspace_id, subject, revision, created_at) "
                "values (:page, :ws, 'shared', 1, :now)"
            ),
            {"page": page_id.hex, "ws": workspace_id.hex, "now": NOW},
        )
        connection.commit()

    command.upgrade(config, REVISION)

    with engine.connect() as connection:
        items = {
            r.id: (r.created_from_page_uid, r.source_uid)
            for r in connection.execute(
                sa.text("select id, created_from_page_uid, source_uid from memory_item")
            )
        }
        link = connection.execute(sa.text("select source_uid, page_uid from memory_source")).one()
        mirror = connection.execute(sa.text("select page_uid from mem_page")).one()
    engine.dispose()
    assert items[derived_id.hex] == (page_uid.hex, source_uid.hex)
    assert items[manual_id.hex] == (None, None)
    assert tuple(link) == (source_uid.hex, page_uid.hex)
    assert mirror[0] == page_uid.hex
