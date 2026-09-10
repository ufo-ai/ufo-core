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

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

BEFORE = "memory_0023"
DATA = "memory_0024"
DDL = "memory_0025"
NOW = "2026-09-10 06:30:00+00:00"
LATER = "2026-09-10 07:00:00+00:00"
LATEST = "2026-09-10 08:00:00+00:00"
RETIRED = "2026-09-10 05:00:00+00:00"
MULTI_BODY = "the launch date is June 12"
SINGLE_BODY = "the auditor is booked for thursday"
TAKEN_BODY = "the ledger closes on friday"
NAMES = (
    "workspace",
    "connection",
    "source",
    "page_1",
    "page_2",
    "page_3",
    "page_4",
    "page_5",
    "unindexed",
    "multi",
    "multi_copy",
    "single",
    "taken",
    "fresh",
)
ITEMS = NAMES[9:]
MEMORY_TABLES = ("memory_item", "memory_source")


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
        path = tmp_path / "page-local-facts-finish.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_finish_{uuid4().hex[:8]}"
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


def _uuid(value: object) -> UUID:
    return UUID(str(value))


def _seed_item(
    connection: sa.Connection,
    ids: dict[str, str],
    item: str,
    body: str,
    page: str | None,
    revision: int | None,
    digest: bool,
    retired: bool = False,
    created_at: str = NOW,
    superseded_by: str | None = None,
) -> None:
    connection.execute(
        sa.text(
            "insert into memory_item (id, workspace_id, subject, body, body_digest, item_class, "
            "memory_kind, confidence, created_from_page_uid, created_from_page_revision, "
            "source_uid, embedding_digest, superseded_by, retired_at, created_at, updated_at) "
            "values (:id, :ws, 'shared', :body, :digest, 'fact', 'fact', 7, :page, :rev, :src, "
            "'sha256:settled', :superseded, :retired, :created, :created)"
        ),
        {
            "id": ids[item],
            "ws": ids["workspace"],
            "body": body,
            "digest": _digest(body) if digest else None,
            "page": None if page is None else ids[page],
            "rev": revision,
            "src": None if page is None else ids["source"],
            "superseded": None if superseded_by is None else ids[superseded_by],
            "retired": RETIRED if retired else None,
            "created": created_at,
        },
    )


def _seed_link(
    connection: sa.Connection,
    ids: dict[str, str],
    item: str,
    page: str,
    revision: int,
    updated_at: str = NOW,
) -> None:
    connection.execute(
        sa.text(
            "insert into memory_source (workspace_id, memory_item_id, source_uid, page_uid, "
            "revision, created_at, updated_at) values (:ws, :item, :src, :page, :rev, :now, "
            ":updated)"
        ),
        {
            "ws": ids["workspace"],
            "item": ids[item],
            "src": ids["source"],
            "page": ids[page],
            "rev": revision,
            "now": NOW,
            "updated": updated_at,
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
    for page in ("page_1", "page_2", "page_3", "page_4", "page_5", "unindexed"):
        connection.execute(
            sa.text(
                "insert into page (uid, workspace_id, source_uid, source_identity, digest, "
                "body_ref, stream, title, subject, tombstone, indexed, created_at, updated_at) "
                "values (:uid, :ws, :src, :identity, 'sha256:p', 'pages/p', 'notes', 'Page', "
                f"'shared', {false}, :indexed, :now, :now)"
            ),
            {
                "uid": ids[page],
                "ws": ids["workspace"],
                "src": ids["source"],
                "identity": f"notes/{page}.md",
                "indexed": (page != "unindexed") if postgres else int(page != "unindexed"),
                "now": NOW,
            },
        )
    connection.execute(
        sa.text(
            "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
            "values (:uid, :ws, 'shared', 1, :now)"
        ),
        {"uid": ids["unindexed"], "ws": ids["workspace"], "now": NOW},
    )
    _seed_item(connection, ids, "multi", MULTI_BODY, "page_1", 1, digest=False, retired=True)
    _seed_link(connection, ids, "multi", "page_1", 1)
    _seed_link(connection, ids, "multi", "page_2", 2)
    _seed_link(connection, ids, "multi", "page_3", 3)
    _seed_item(
        connection,
        ids,
        "multi_copy",
        MULTI_BODY,
        "page_2",
        2,
        digest=True,
        retired=True,
        created_at=LATER,
    )
    _seed_item(connection, ids, "single", SINGLE_BODY, "page_4", 4, digest=False)
    _seed_link(connection, ids, "single", "page_4", 4)
    _seed_item(connection, ids, "taken", TAKEN_BODY, "page_5", 5, digest=False)
    _seed_link(connection, ids, "taken", "page_5", 5)
    _seed_item(connection, ids, "fresh", TAKEN_BODY, "page_5", 5, digest=True, created_at=LATER)


def _items(connection: sa.Connection) -> dict[UUID, sa.Row]:
    return {
        _uuid(row.id): row
        for row in connection.execute(
            sa.text(
                "select id, body, body_digest, created_from_page_uid, created_from_page_revision, "
                "source_uid, embedding_digest, retired_at, superseded_by from memory_item"
            )
        ).all()
    }


def _digest_nullable(connection: sa.Connection) -> bool:
    return next(
        bool(column["nullable"])
        for column in sa.inspect(connection).get_columns("memory_item")
        if column["name"] == "body_digest"
    )


def _prepared(migration_urls: tuple[str, str]) -> tuple[Config, sa.Engine, dict[str, str], bool]:
    migration_url, sync_url = migration_urls
    postgres = migration_url.startswith("postgresql")
    config = _alembic(migration_url)
    command.upgrade(config, BEFORE)
    ids = {name: (str(uuid4()) if postgres else uuid4().hex) for name in NAMES}
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection, ids, postgres)
        connection.commit()
    return config, engine, ids, postgres


def test_legacy_rows_settle_and_the_link_table_goes_in_its_own_revision(
    migration_urls: tuple[str, str],
) -> None:
    """The data revision: a retired legacy row linked to three pages, one already holding a copy,
    is adopted for its own page with its digest and its stamp, the uncopied page gets a copy
    carrying the stamp and the link's revision, the copied page keeps its copy; a legacy single-link
    row is adopted; a legacy row whose page already holds a fresh row is deleted; the workspace
    mirroring an unindexed page carries the drain marker again; `memory_source` and the nullable
    column are untouched. The DDL revision then drops the table and leaves the column nullable; its
    downgrade recreates the table empty."""
    config, engine, ids, _postgres = _prepared(migration_urls)

    command.upgrade(config, DATA)
    with engine.connect() as connection:
        rows = _items(connection)
        nullable_after_data = _digest_nullable(connection)
        tables_after_data = set(sa.inspect(connection).get_table_names())
        markers = connection.execute(
            sa.text(
                "select workspace_id from ext_store where extension = 'memory' "
                "and key = 'unindexed_pages_drain'"
            )
        ).all()

    seeded = {name: _uuid(ids[name]) for name in ITEMS}
    adopted = rows[seeded["multi"]]
    assert (adopted.body_digest, adopted.retired_at is not None) == (_digest(MULTI_BODY), True)
    assert _uuid(adopted.created_from_page_uid) == _uuid(ids["page_1"])
    assert rows[seeded["multi_copy"]].body_digest == _digest(MULTI_BODY)
    copies = [item for item in rows if item not in seeded.values()]
    assert len(copies) == 1
    copy = rows[copies[0]]
    assert (
        copy.body,
        copy.body_digest,
        _uuid(copy.created_from_page_uid),
        copy.created_from_page_revision,
        _uuid(copy.source_uid),
        copy.embedding_digest,
        copy.retired_at is not None,
    ) == (
        MULTI_BODY,
        _digest(MULTI_BODY),
        _uuid(ids["page_3"]),
        3,
        _uuid(ids["source"]),
        None,
        True,
    )
    assert rows[seeded["single"]].body_digest == _digest(SINGLE_BODY)
    assert seeded["taken"] not in rows
    assert rows[seeded["fresh"]].body_digest == _digest(TAKEN_BODY)
    assert all(row.body_digest is not None for row in rows.values())
    assert nullable_after_data is True
    assert set(MEMORY_TABLES) <= tables_after_data
    assert [_uuid(marker.workspace_id) for marker in markers] == [_uuid(ids["workspace"])]

    command.upgrade(config, DDL)
    with engine.connect() as connection:
        nullable = _digest_nullable(connection)
        tables = set(sa.inspect(connection).get_table_names())
    assert nullable is True
    assert "memory_source" not in tables

    command.downgrade(config, DATA)
    with engine.connect() as connection:
        restored_tables = set(sa.inspect(connection).get_table_names())
        links = connection.execute(sa.text("select count(*) from memory_source")).scalar_one()
    engine.dispose()
    assert "memory_source" in restored_tables
    assert links == 0


def test_the_data_steps_refuse_while_a_link_is_newer_than_every_digested_row(
    migration_urls: tuple[str, str],
) -> None:
    """A `memory_source` link later than the newest digest-bearing row was written by the release
    before `memory_0023`'s code, which never touches the table: the revision names the deploy order
    instead of dropping the table under it."""
    config, engine, ids, _postgres = _prepared(migration_urls)
    with engine.connect() as connection:
        _seed_link(connection, ids, "single", "page_1", 1, updated_at=LATEST)
        connection.commit()
    engine.dispose()

    with pytest.raises(RuntimeError, match="80147652e"):
        command.upgrade(config, DATA)


def test_the_data_steps_pass_on_an_empty_database(migration_urls: tuple[str, str]) -> None:
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, DDL)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        tables = set(sa.inspect(connection).get_table_names())
    engine.dispose()
    assert "memory_item" in tables
    assert "memory_source" not in tables


def test_the_data_revision_locks_the_table_before_it_reads_or_writes_it(
    migration_urls: tuple[str, str],
) -> None:
    """Live pods queue behind the data steps rather than racing them, so the lock has to be the
    first statement that names a memory table; a read before it would leave a window for a commit
    to take a slot the steps are about to fill."""
    config, engine, _ids, postgres = _prepared(migration_urls)
    engine.dispose()
    if not postgres:
        pytest.skip("SQLite's single writer is the lock")
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        command.upgrade(config, DATA)
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)

    touching = [
        statement
        for statement in statements
        if any(table in statement.lower() for table in MEMORY_TABLES)
    ]
    assert touching[0].lower().strip() == "lock table memory_item in exclusive mode"
