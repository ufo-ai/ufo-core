import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

CORE_HEAD = "20260909062725"
BEFORE = "memory_0018"
REVISION = "memory_0019"
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


def _seed_page(connection: sa.Connection, workspace: str, source: str, page: str, uid: str) -> None:
    connection.execute(
        sa.text(
            "insert into page (id, uid, workspace_id, source_id, digest, body_ref, stream, "
            "title, subject, tombstone, created_at, updated_at) values "
            "(:id, :uid, :ws, :src, 'sha256:p', 'pages/p', 'notes', 'Page', 'shared', 0, "
            ":now, :now)"
        ),
        {"id": page, "uid": uid, "ws": workspace, "src": source, "now": NOW},
    )


def _seed_item(
    connection: sa.Connection,
    workspace: str,
    item: str,
    page: str | None,
    page_uid: str | None,
    source: str | None,
    source_uid: str | None,
) -> None:
    connection.execute(
        sa.text(
            "insert into memory_item (id, workspace_id, subject, body, item_class, memory_kind, "
            "confidence, created_from_page_id, created_from_page_uid, created_from_page_revision, "
            "source_id, source_uid, embedding_digest, created_at, updated_at) values (:id, :ws, "
            "'shared', :body, 'fact', 'fact', 5, :page, :page_uid, :rev, :src, :src_uid, "
            "'sha256:settled', :now, :now)"
        ),
        {
            "id": item,
            "ws": workspace,
            "body": f"fact {item}",
            "page": page,
            "page_uid": page_uid,
            "rev": 1 if page else None,
            "src": source,
            "src_uid": source_uid,
            "now": NOW,
        },
    )


def _seed_link(
    connection: sa.Connection,
    workspace: str,
    item: str,
    source: str,
    source_uid: str | None,
    page: str,
    page_uid: str | None,
) -> None:
    connection.execute(
        sa.text(
            "insert into memory_source (workspace_id, memory_item_id, source_id, source_uid, "
            "page_id, page_uid, revision, created_at, updated_at) values "
            "(:ws, :item, :src, :src_uid, :page, :page_uid, 1, :now, :now)"
        ),
        {
            "ws": workspace,
            "item": item,
            "src": source,
            "src_uid": source_uid,
            "page": page,
            "page_uid": page_uid,
            "now": NOW,
        },
    )


def test_twins_become_the_keys_and_orphans_are_settled(tmp_path: Path) -> None:
    """Four rows meet the revision: a fact whose twins agree with its ids, a fact the replaced
    release re-pointed to a second page without touching the twin (re-synced from the id), a fact
    whose page is gone (retired, binding cleared), and a link whose page is gone (dropped). Then the
    keys: a mirror row keyed by `page_uid` alone lands, a duplicate of it is refused, and the
    check over the twins refuses a half-bound row."""
    database = tmp_path / "reads-on-uid.db"
    config = _config(database)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, BEFORE)

    workspace, connection_id, source, source_uid = (uuid4().hex for _ in range(4))
    page_a, uid_a, page_b, uid_b, gone_page = (uuid4().hex for _ in range(5))
    agreed, repointed, orphaned = (uuid4().hex for _ in range(3))
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into connection (id, workspace_id, provider, account_id, host, shared, "
                "created_at, updated_at) values (:id, :ws, 'folder', '', '', 1, :now, :now)"
            ),
            {"id": connection_id, "ws": workspace, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
                "connection_id, next_sync_at, created_at, updated_at) values "
                "(:id, :uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
            ),
            {"id": source, "uid": source_uid, "ws": workspace, "conn": connection_id, "now": NOW},
        )
        _seed_page(connection, workspace, source, page_a, uid_a)
        _seed_page(connection, workspace, source, page_b, uid_b)
        _seed_item(connection, workspace, agreed, page_a, uid_a, source, source_uid)
        _seed_item(connection, workspace, repointed, page_b, uid_a, source, source_uid)
        _seed_item(connection, workspace, orphaned, gone_page, None, source, source_uid)
        _seed_link(connection, workspace, agreed, source, source_uid, page_a, uid_a)
        _seed_link(connection, workspace, repointed, source, source_uid, page_b, uid_b)
        _seed_link(connection, workspace, orphaned, source, source_uid, gone_page, None)
        connection.execute(
            sa.text(
                "insert into mem_page (page_id, page_uid, workspace_id, subject, revision, "
                "created_at) values (:page, :uid, :ws, 'shared', 1, :now)"
            ),
            {"page": page_a, "uid": uid_a, "ws": workspace, "now": NOW},
        )
        connection.commit()

    command.upgrade(config, REVISION)

    with engine.connect() as connection:
        items = {
            r.id: (
                r.created_from_page_uid,
                r.created_from_page_revision,
                r.source_uid,
                r.retired_at,
            )
            for r in connection.execute(
                sa.text(
                    "select id, created_from_page_uid, created_from_page_revision, source_uid, "
                    "retired_at from memory_item"
                )
            )
        }
        orphan_ids = connection.execute(
            sa.text("select created_from_page_id, source_id from memory_item where id = :id"),
            {"id": orphaned},
        ).one()
        links = {
            r.memory_item_id: (r.source_uid, r.page_uid, r.page_id)
            for r in connection.execute(
                sa.text("select memory_item_id, source_uid, page_uid, page_id from memory_source")
            )
        }
        settled_digest = connection.execute(
            sa.text("select embedding_digest from memory_item where id = :id"), {"id": orphaned}
        ).scalar_one()
        connection.execute(
            sa.text(
                "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
                "values (:uid, :ws, 'shared', 1, :now)"
            ),
            {"uid": uid_b, "ws": workspace, "now": NOW},
        )
        connection.commit()
        try:
            connection.execute(
                sa.text(
                    "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
                    "values (:uid, :ws, 'shared', 2, :now)"
                ),
                {"uid": uid_b, "ws": workspace, "now": NOW},
            )
        except sa.exc.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("a second mirror row for one page_uid landed")
        try:
            _seed_item(connection, workspace, uuid4().hex, None, uid_a, None, None)
        except sa.exc.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("a row bound to a page uid with no revision landed")
        mirrors = connection.execute(
            sa.text("select page_uid, page_id from mem_page order by page_uid")
        ).all()
    engine.dispose()

    assert items[agreed] == (uid_a, 1, source_uid, None)
    assert items[repointed][:3] == (uid_b, 1, source_uid) and items[repointed][3] is None
    assert items[orphaned][:3] == (None, None, None) and items[orphaned][3] is not None
    assert settled_digest is None and tuple(orphan_ids) == (None, None)
    assert links == {agreed: (source_uid, uid_a, page_a), repointed: (source_uid, uid_b, page_b)}
    assert sorted(mirrors) == sorted([(uid_a, page_a), (uid_b, None)])
