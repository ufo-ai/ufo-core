import asyncio
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
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import (
    MemoryStore,
    MemoryWrite,
    memory_item,
)

from ufo.db import MIGRATIONS_DIR, dispose_db, init_db, workspace_tx
from ufo.host.ext.loader import migration_locations
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws

DERIVED_BODY = "the acme renewal closes on september 30"
MANUAL_BODY = "the office wifi password rotates monthly"
PAGELESS_BODY = "the pilot ended in march"
UNREPLAYED_BODY = "the security review slipped a week"
CONTENT_IDS_UNWRITTEN = "20260909204543"


class _UnreachedEmbed:
    """A commit derives nothing, so the store never reaches its embed backend — a call here is the
    write path deriving state the index job owns."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("commit embeds nothing")


async def _replay_derivation(
    migration_url: str, workspace_id: UUID, page_uid: UUID, source_uid: UUID
) -> list[tuple[UUID, UUID]]:
    """Deliver the same page change twice through the real store — the at-least-once replay a
    restarted page-change cursor performs, naming the page and the source by the uid every citer
    reads them by — and read back what the derived rows carry."""
    init_db(migration_url)
    try:
        with ws(workspace_id):
            store = MemoryStore(
                index=DefaultIndex(transaction=workspace_tx),
                embed=_UnreachedEmbed(),
                transaction=workspace_tx,
                workspace_id=workspace_id,
                page_states=context_for("memory", frozenset()).page_states,
            )
            for _ in range(2):
                await store.commit(
                    MemoryWrite(
                        subject="shared",
                        body=DERIVED_BODY,
                        created_from_page_id=page_uid,
                        created_from_page_revision=1,
                        source_id=source_uid,
                    )
                )
            async with workspace_tx() as connection:
                return [
                    (row.id, row.source_uid)
                    for row in (
                        await connection.execute(
                            sa.select(memory_item.c.id, memory_item.c.source_uid).where(
                                memory_item.c.created_from_page_uid == page_uid
                            )
                        )
                    ).all()
                ]
    finally:
        await dispose_db()


def _alembic(migration_url: str) -> Config:
    """The real migration environment over one throwaway database: core versions plus every
    installed extension's location, so an extension revision resolves its core dependency."""
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
            connection.exec_driver_sql(f'drop database "{database}" with (force)')
        admin.dispose()


def test_page_revision_migration_invalidates_derivations_and_requests_full_replay(
    migration_urls: tuple[str, str],
) -> None:
    """`memory_0010` binds derivations to a page revision and asks every page-change consumer to
    replay; `memory_0012` records each derivation's source as a link. Every seeded row keeps its id
    across the migration — nothing is re-keyed, nothing in the index is orphaned — and a page
    derivation that resolves a complete origin gains one `memory_source` link from it. The
    derivations the replay has not reached (`memory_0010` backfills no revision) and one whose page
    is gone cannot form a complete origin, so their origin is cleared and they take no link rather
    than break the check; a later replay lands one page-local row for the page beside the rows the
    migration kept."""
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, "0053")
    command.upgrade(config, "memory_0009")

    engine = sa.create_engine(sync_url)
    workspace_id, source_id, page_id, later_page_id, manual_id, pageless_id = (
        uuid4() for _ in range(6)
    )
    derived_id, unreplayed_id = uuid4(), uuid4()
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
        for item_id, body, page_origin in (
            (derived_id, DERIVED_BODY, page_id.hex),
            (unreplayed_id, UNREPLAYED_BODY, later_page_id.hex),
            (manual_id, MANUAL_BODY, None),
        ):
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
                    "body": body,
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

    command.upgrade(config, "index_default_0002")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "update memory_item set created_from_page_revision = 1, "
                "embedding_digest = 'sha256:derived' where id = :id"
            ),
            {"id": derived_id.hex},
        )
        connection.execute(
            sa.text(
                "insert into memory_item "
                "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                "created_from_page_id, created_from_page_revision, embedding_digest, "
                "created_at, updated_at) values "
                "(:id, :workspace_id, 'shared', :body, 'fact', 'fact', 5, :page_id, 1, "
                "'sha256:pageless', :now, :now)"
            ),
            {
                "id": pageless_id.hex,
                "workspace_id": workspace_id.hex,
                "body": PAGELESS_BODY,
                "page_id": uuid4().hex,
                "now": now,
            },
        )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "memory_0012")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        items = {
            UUID(str(row.id)): (
                None if row.created_from_page_id is None else UUID(str(row.created_from_page_id)),
                None if row.source_id is None else UUID(str(row.source_id)),
                row.embedding_digest,
            )
            for row in connection.execute(
                sa.text(
                    "select id, created_from_page_id, source_id, embedding_digest from memory_item"
                )
            ).all()
        }
        links = {
            (
                UUID(str(row.memory_item_id)),
                UUID(str(row.source_id)),
                UUID(str(row.page_id)),
                row.revision,
            )
            for row in connection.execute(
                sa.text("select memory_item_id, source_id, page_id, revision from memory_source")
            ).all()
        }
        bodies = set(connection.execute(sa.text("select body from memory_item")).scalars())
    engine.dispose()

    assert items[derived_id] == (page_id, source_id, "sha256:derived")
    assert items[pageless_id] == (None, None, "sha256:pageless")
    assert items[manual_id] == (None, None, "sha256:embedding")
    assert links == {(derived_id, source_id, page_id, 1)}
    assert bodies == {DERIVED_BODY, UNREPLAYED_BODY, MANUAL_BODY, PAGELESS_BODY}

    # `0056` refuses a workspace with no member and no agent, so this seed supplies one each. The
    # store speaks uid, minted by the backfill and read at the last revision keeping the seeded ids.
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :ws, 'admin@work.com', :now, :now)"
            ),
            {"id": uuid4().hex, "ws": workspace_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
                "values (:id, :ws, 'main', 'p', 'auto', :now, :now)"
            ),
            {"id": uuid4().hex, "ws": workspace_id.hex, "now": now},
        )
        connection.commit()
    engine.dispose()
    command.upgrade(config, CONTENT_IDS_UNWRITTEN)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        minted = connection.execute(
            sa.text(
                "select page.uid as page_uid, source.uid as source_uid from page "
                "join source on source.id = page.source_id where page.id = :id"
            ),
            {"id": page_id.hex},
        ).one()
    engine.dispose()
    command.upgrade(config, "heads")
    page_uid, source_uid = UUID(str(minted.page_uid)), UUID(str(minted.source_uid))
    rebuilt = asyncio.run(_replay_derivation(migration_url, workspace_id, page_uid, source_uid))
    assert [source for _row_id, source in rebuilt] == [source_uid]
    assert rebuilt[0][0] != derived_id

    command.downgrade(config, "memory_0011")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("memory_item")}
        table_present = inspector.has_table("memory_source")
        ids = {
            UUID(str(stored_id))
            for stored_id in connection.execute(sa.text("select id from memory_item")).scalars()
        }
    engine.dispose()
    assert "source_id" not in columns
    assert table_present is False
    assert {derived_id, unreplayed_id, manual_id, pageless_id} <= ids


def test_source_partition_backfills_one_link_per_source_without_rekeying(
    migration_urls: tuple[str, str],
) -> None:
    """The id stays content-addressed over `(workspace, subject, item_class, body)`, so a derived
    row seeded under that id keeps it across `memory_0012` — nothing is re-keyed, so nothing in the
    index is orphaned — and the row gains exactly one `memory_source` link from its page's source.
    A row whose page is gone cannot form a complete origin, so its origin is cleared and it takes no
    link rather than break the source-complete check."""
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, "0054")
    command.upgrade(config, "memory_0011")

    workspace_id, source_id, page_id = uuid4(), uuid4(), uuid4()
    live_id, orphan_id = uuid4(), uuid4()
    now = datetime(2026, 7, 27, tzinfo=UTC)
    engine = sa.create_engine(sync_url)
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
        for item_id, body, origin_page in (
            (live_id, DERIVED_BODY, page_id.hex),
            (orphan_id, PAGELESS_BODY, uuid4().hex),
        ):
            connection.execute(
                sa.text(
                    "insert into memory_item "
                    "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                    "created_from_page_id, created_from_page_revision, embedding_digest, "
                    "created_at, updated_at) values "
                    "(:id, :workspace_id, 'shared', :body, 'fact', 'fact', 5, :page, 1, "
                    "'sha256:e', :now, :now)"
                ),
                {
                    "id": item_id.hex,
                    "workspace_id": workspace_id.hex,
                    "body": body,
                    "page": origin_page,
                    "now": now,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "memory_0012")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        ids = {
            UUID(str(stored_id))
            for stored_id in connection.execute(sa.text("select id from memory_item")).scalars()
        }
        links = {
            (UUID(str(row.memory_item_id)), UUID(str(row.source_id)))
            for row in connection.execute(
                sa.text("select memory_item_id, source_id from memory_source")
            ).all()
        }
        orphan_origin = connection.execute(
            sa.text(
                "select created_from_page_id, created_from_page_revision, source_id "
                "from memory_item where id = :id"
            ),
            {"id": orphan_id.hex},
        ).one()
    engine.dispose()
    assert ids == {live_id, orphan_id}
    assert links == {(live_id, source_id)}
    assert (orphan_origin.created_from_page_id, orphan_origin.created_from_page_revision) == (
        None,
        None,
    )
    assert orphan_origin.source_id is None


def test_mem_page_cascade_collects_a_mirror_whose_page_is_gone(
    migration_urls: tuple[str, str],
) -> None:
    """`memory_0017` puts a foreign key over `mem_page.page_id`, and a key cannot be created while a
    mirror names a page that is gone — so the upgrade collects those rows first. A mirror whose page
    still stands survives it. What the key then does with a live page is `test_mem_page_cascade`'s,
    which drives the app's own engine — this one's raw engine never runs SQLite's foreign-key
    pragma."""
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, "0054")
    command.upgrade(config, "memory_0016")

    workspace_id, source_id, page_id = uuid4(), uuid4(), uuid4()
    gone_page_id = uuid4()
    now = datetime(2026, 9, 9, tzinfo=UTC)
    engine = sa.create_engine(sync_url)
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
        for mirror_page_id in (page_id, gone_page_id):
            connection.execute(
                sa.text(
                    "insert into mem_page "
                    "(page_id, workspace_id, subject, revision, created_at) values "
                    "(:page_id, :workspace_id, 'shared', 1, :now)"
                ),
                {
                    "page_id": mirror_page_id.hex,
                    "workspace_id": workspace_id.hex,
                    "now": now,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "memory_0017")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        mirrors = {
            UUID(str(stored))
            for stored in connection.execute(sa.text("select page_id from mem_page")).scalars()
        }
        assert mirrors == {page_id}
    engine.dispose()
