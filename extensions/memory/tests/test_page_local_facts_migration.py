import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

BEFORE = "memory_0022"
REVISION = "memory_0023"
NOW = "2026-09-10 06:30:00+00:00"
INDEXES = {"memory_item_page_body", "memory_item_written_body"}
SHARED_BODY = "the launch date is June 12"
OWN_BODY = "the auditor is booked for thursday"
WRITTEN_BODY = "the office wifi password rotates monthly"
OUTGOING_COMMIT = sa.text(
    "insert into memory_item (id, workspace_id, subject, body, item_class, memory_kind, "
    "confidence, created_from_page_uid, created_from_page_revision, source_uid, created_at, "
    "updated_at) values (:id, :ws, 'shared', :body, 'fact', 'fact', 5, :page, :rev, :src, :now, "
    ":now) on conflict (id) do update set "
    "created_from_page_uid = excluded.created_from_page_uid, "
    "created_from_page_revision = excluded.created_from_page_revision, "
    "source_uid = excluded.source_uid, updated_at = excluded.updated_at"
)
OUTGOING_LINK = sa.text(
    "insert into memory_source (workspace_id, memory_item_id, source_uid, page_uid, revision, "
    "created_at, updated_at) values (:ws, :item, :src, :page, :rev, :now, :now) "
    "on conflict (memory_item_id, page_uid) do update set revision = excluded.revision"
)
OUTGOING_REPOINT = sa.text(
    "update memory_item set created_from_page_uid = :page, created_from_page_revision = :rev, "
    "source_uid = :src, embedding_digest = null, embedding_claimed_at = null where id = :id"
)
NEW_IMAGE_PAGE_ROW = sa.text(
    "insert into memory_item (id, workspace_id, subject, body, body_digest, item_class, "
    "memory_kind, confidence, created_from_page_uid, created_from_page_revision, source_uid, "
    "created_at, updated_at) values (:id, :ws, 'shared', :body, :digest, 'fact', 'fact', 5, "
    ":page, :rev, :src, :now, :now)"
)
NEW_IMAGE_WRITTEN_ROW = sa.text(
    "insert into memory_item (id, workspace_id, subject, body, body_digest, item_class, "
    "memory_kind, confidence, created_at, updated_at) values (:id, :ws, 'shared', :body, "
    ":digest, 'fact', 'fact', 5, :now, :now)"
)
NEW_IMAGE_SUPERSEDE = sa.text(
    "delete from memory_item where workspace_id = :ws and created_from_page_uid = :page "
    "and body_digest is not null and id <> :kept"
)


def _alembic(migration_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url)
    return config


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "page-local-facts.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_local_{uuid4().hex[:8]}"
    admin = sa.create_engine(
        source.set(drivername="postgresql+psycopg", database="ufo"), isolation_level="AUTOCOMMIT"
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
            connection.exec_driver_sql(f'drop database "{database}" with (force)')
        admin.dispose()


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _seed_item(
    connection: sa.Connection,
    ids: dict[str, str],
    item: str,
    body: str,
    page: str | None,
    revision: int | None,
) -> None:
    connection.execute(
        sa.text(
            "insert into memory_item (id, workspace_id, subject, body, item_class, memory_kind, "
            "confidence, created_from_page_uid, created_from_page_revision, source_uid, "
            "embedding_digest, created_at, updated_at) values (:id, :ws, 'shared', :body, 'fact', "
            "'fact', 7, :page, :rev, :src, 'sha256:settled', :now, :now)"
        ),
        {
            "id": ids[item],
            "ws": ids["workspace"],
            "body": body,
            "page": page,
            "rev": revision,
            "src": None if page is None else ids["source"],
            "now": NOW,
        },
    )


def _seed_link(
    connection: sa.Connection, ids: dict[str, str], item: str, page: str, revision: int
) -> None:
    connection.execute(
        OUTGOING_LINK,
        {
            "ws": ids["workspace"],
            "item": ids[item],
            "src": ids["source"],
            "page": ids[page],
            "rev": revision,
            "now": NOW,
        },
    )


def _seed(connection: sa.Connection, ids: dict[str, str], postgres: bool) -> None:
    true, false = ("true", "false") if postgres else ("1", "0")
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": ids["workspace"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into connection (id, workspace_id, provider, account_id, host, shared, "
            f"created_at, updated_at) values (:id, :ws, 'folder', '', '', {true}, :now, :now)"
        ),
        {"id": ids["connection"], "ws": ids["workspace"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into source (uid, workspace_id, backend, config, feed_handle, connection_id, "
            "next_sync_at, created_at, updated_at) values "
            "(:uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
        ),
        {"uid": ids["source"], "ws": ids["workspace"], "conn": ids["connection"], "now": NOW},
    )
    for page in ("page_1", "page_2", "page_3", "page_4"):
        connection.execute(
            sa.text(
                "insert into page (uid, workspace_id, source_uid, source_identity, digest, "
                "body_ref, stream, title, subject, tombstone, created_at, updated_at) values "
                f"(:uid, :ws, :src, :identity, 'sha256:p', 'pages/p', 'notes', 'Page', 'shared', "
                f"{false}, :now, :now)"
            ),
            {
                "uid": ids[page],
                "ws": ids["workspace"],
                "src": ids["source"],
                "identity": f"notes/{page}.md",
                "now": NOW,
            },
        )
    _seed_item(connection, ids, "shared", SHARED_BODY, ids["page_1"], 1)
    _seed_link(connection, ids, "shared", "page_1", 1)
    _seed_link(connection, ids, "shared", "page_2", 2)
    _seed_link(connection, ids, "shared", "page_3", 3)
    _seed_item(connection, ids, "own", OWN_BODY, ids["page_4"], 4)
    _seed_link(connection, ids, "own", "page_4", 4)
    _seed_item(connection, ids, "written", WRITTEN_BODY, None, None)


def _items(connection: sa.Connection) -> dict[UUID, sa.Row]:
    return {
        _uuid(row.id): row
        for row in connection.execute(
            sa.text(
                "select id, body, body_digest, confidence, created_from_page_uid, "
                "created_from_page_revision, source_uid, embedding_digest, embedding_claimed_at "
                "from memory_item"
            )
        ).all()
    }


def _indexes(connection: sa.Connection) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(connection).get_indexes("memory_item")}


def _uuid(value: object) -> UUID:
    return UUID(str(value))


def _new_id(postgres: bool) -> str:
    return str(uuid4()) if postgres else uuid4().hex


def test_multi_link_items_split_into_copies_and_the_roll_never_collides(
    migration_urls: tuple[str, str],
) -> None:
    """Three items meet the revision: a fact one page derived and two more pages linked, a fact
    linked only by its own page, and a fact a conversation wrote. No existing row gains a digest.
    The multi-link item's three links, its own page's included, become page-local copies carrying
    the digest, the link's page, revision and source, and no embedding digest; the other two items
    take no copy. With the new image's fresh rows already holding the single-link page's slot and
    the written slot, the outgoing image's `commit` upsert and `supersede_page_facts` re-point run
    against every seeded item without a collision, and the new image's derivation of the page the
    legacy row sits on deletes nothing but digest-bearing rows: the legacy row and the copy stand
    side by side, one statement to every read. The two partial unique indexes refuse a second
    row per statement per page and a second pageless row per statement. The downgrade gives every
    linkless page-derived row one link from its own binding; the upgrade after it copies nothing,
    since no item is multi-linked any more, and leaves every digest NULL for the follow-up."""
    migration_url, sync_url = migration_urls
    postgres = migration_url.startswith("postgresql")
    config = _alembic(migration_url)
    command.upgrade(config, BEFORE)
    names = (
        "workspace",
        "connection",
        "source",
        "page_1",
        "page_2",
        "page_3",
        "page_4",
        "shared",
        "own",
        "written",
    )
    ids = {name: _new_id(postgres) for name in names}
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection, ids, postgres)
        connection.commit()

    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        rows = _items(connection)
        indexes = _indexes(connection)

    seeded = {name: _uuid(ids[name]) for name in ("shared", "own", "written")}
    assert all(rows[item].body_digest is None for item in seeded.values())
    copies = {item: row for item, row in rows.items() if item not in seeded.values()}
    assert len(copies) == 3
    assert {
        (
            row.body,
            row.body_digest,
            row.confidence,
            _uuid(row.created_from_page_uid),
            row.created_from_page_revision,
            _uuid(row.source_uid),
            row.embedding_digest,
            row.embedding_claimed_at,
        )
        for row in copies.values()
    } == {
        (
            SHARED_BODY,
            _digest(SHARED_BODY),
            7,
            _uuid(ids[page]),
            revision,
            _uuid(ids["source"]),
            None,
            None,
        )
        for page, revision in (("page_1", 1), ("page_2", 2), ("page_3", 3))
    }
    assert INDEXES <= indexes
    copy_on_page_3 = next(
        item
        for item, row in copies.items()
        if _uuid(row.created_from_page_uid) == _uuid(ids["page_3"])
    )

    fresh_own, fresh_written = _new_id(postgres), _new_id(postgres)
    with engine.connect() as connection:
        connection.execute(
            NEW_IMAGE_PAGE_ROW,
            {
                "id": fresh_own,
                "ws": ids["workspace"],
                "body": OWN_BODY,
                "digest": _digest(OWN_BODY),
                "page": ids["page_4"],
                "rev": 4,
                "src": ids["source"],
                "now": NOW,
            },
        )
        connection.execute(
            NEW_IMAGE_WRITTEN_ROW,
            {
                "id": fresh_written,
                "ws": ids["workspace"],
                "body": WRITTEN_BODY,
                "digest": _digest(WRITTEN_BODY),
                "now": NOW,
            },
        )
        for item, body, page, revision in (
            ("own", OWN_BODY, "page_4", 4),
            ("written", WRITTEN_BODY, None, None),
            ("shared", SHARED_BODY, "page_2", 2),
        ):
            connection.execute(
                OUTGOING_COMMIT,
                {
                    "id": ids[item],
                    "ws": ids["workspace"],
                    "body": body,
                    "page": None if page is None else ids[page],
                    "rev": revision,
                    "src": None if page is None else ids["source"],
                    "now": NOW,
                },
            )
        _seed_link(connection, ids, "shared", "page_2", 2)
        connection.execute(
            OUTGOING_REPOINT,
            {"id": ids["shared"], "page": ids["page_3"], "rev": 3, "src": ids["source"]},
        )
        connection.commit()
        rebound = _items(connection)
    assert (
        _uuid(rebound[seeded["shared"]].created_from_page_uid),
        rebound[seeded["shared"]].created_from_page_revision,
    ) == (_uuid(ids["page_3"]), 3)
    assert _uuid(rebound[seeded["own"]].created_from_page_uid) == _uuid(ids["page_4"])
    assert all(rebound[item].body_digest is None for item in seeded.values())
    assert len(rebound) == 8

    with engine.connect() as connection:
        connection.execute(
            NEW_IMAGE_SUPERSEDE,
            {
                "ws": ids["workspace"],
                "page": ids["page_3"],
                "kept": str(copy_on_page_3) if postgres else copy_on_page_3.hex,
            },
        )
        connection.commit()
        after_supersede = _items(connection)
    assert seeded["shared"] in after_supersede
    assert set(copies) <= set(after_supersede)
    assert len(after_supersede) == 8

    with pytest.raises(IntegrityError), engine.connect() as connection:
        connection.execute(
            NEW_IMAGE_PAGE_ROW,
            {
                "id": _new_id(postgres),
                "ws": ids["workspace"],
                "body": SHARED_BODY,
                "digest": _digest(SHARED_BODY),
                "page": ids["page_2"],
                "rev": 2,
                "src": ids["source"],
                "now": NOW,
            },
        )
    with pytest.raises(IntegrityError), engine.connect() as connection:
        connection.execute(
            NEW_IMAGE_WRITTEN_ROW,
            {
                "id": _new_id(postgres),
                "ws": ids["workspace"],
                "body": WRITTEN_BODY,
                "digest": _digest(WRITTEN_BODY),
                "now": NOW,
            },
        )

    command.downgrade(config, BEFORE)
    with engine.connect() as connection:
        columns = {column["name"] for column in sa.inspect(connection).get_columns("memory_item")}
        downgraded_indexes = _indexes(connection)
        page_rows = connection.execute(
            sa.text("select id from memory_item where created_from_page_uid is not null")
        ).all()
        links = connection.execute(
            sa.text("select memory_item_id, page_uid, revision, source_uid from memory_source")
        ).all()
    assert "body_digest" not in columns
    assert not INDEXES & downgraded_indexes
    assert len(page_rows) == 6
    assert {_uuid(row.id) for row in page_rows} <= {_uuid(link.memory_item_id) for link in links}
    assert {
        (_uuid(link.page_uid), link.revision, _uuid(link.source_uid))
        for link in links
        if _uuid(link.memory_item_id) in copies
    } == {
        (_uuid(ids[page]), revision, _uuid(ids["source"]))
        for page, revision in (("page_1", 1), ("page_2", 2), ("page_3", 3))
    }

    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        again = _items(connection)
        restored = _indexes(connection)
    engine.dispose()
    assert set(again) == set(after_supersede)
    assert all(row.body_digest is None for row in again.values())
    assert INDEXES <= restored
