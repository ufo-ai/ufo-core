from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260909233938"
REVISION = "20260910114436"
NOW = "2026-09-10 12:00:00+00:00"
PAGES = (
    ("github", "workflow_runs"),
    ("github", "stargazers"),
    ("github", "contributor_activity"),
    ("github", "pull_requests"),
    ("folder", "workflow_runs"),
)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(engine: sa.Engine) -> dict[tuple[str, str], str]:
    workspace_id, connection_id = uuid4(), uuid4()
    sources = {backend: uuid4() for backend in ("github", "folder")}
    pages: dict[tuple[str, str], str] = {}
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into connection (id, workspace_id, provider, account_id, host, shared, "
                "created_at, updated_at) values (:id, :ws, 'github', '', '', 1, :now, :now)"
            ),
            {"id": connection_id.hex, "ws": workspace_id.hex, "now": NOW},
        )
        for backend, source_uid in sources.items():
            connection.execute(
                sa.text(
                    "insert into source (uid, workspace_id, backend, config, feed_handle, "
                    "connection_id, next_sync_at, created_at, updated_at) values "
                    "(:uid, :ws, :backend, '{}', :backend, :conn, :now, :now, :now)"
                ),
                {
                    "uid": source_uid.hex,
                    "ws": workspace_id.hex,
                    "backend": backend,
                    "conn": connection_id.hex,
                    "now": NOW,
                },
            )
        for backend, stream in PAGES:
            page_uid = uuid4()
            pages[(backend, stream)] = page_uid.hex
            connection.execute(
                sa.text(
                    "insert into page (uid, workspace_id, source_uid, digest, body_ref, stream, "
                    "title, subject, tombstone, created_at, updated_at) values "
                    "(:uid, :ws, :src, :digest, 'pages/p', :stream, 'Page', 'shared', 0, "
                    ":now, :now)"
                ),
                {
                    "uid": page_uid.hex,
                    "ws": workspace_id.hex,
                    "src": sources[backend].hex,
                    "digest": f"sha256:{page_uid.hex}",
                    "stream": stream,
                    "now": NOW,
                },
            )
        connection.commit()
    return pages


def _rows(engine: sa.Engine, columns: str) -> dict[str, tuple[object, ...]]:
    with engine.connect() as connection:
        return {
            row[0]: tuple(row[1:])
            for row in connection.execute(sa.text(f"select uid, {columns} from page"))
        }


def test_githubs_unindexed_streams_are_backfilled_false_and_nothing_else_moves(
    tmp_path: Path,
) -> None:
    """Rows GitHub's three unindexed streams landed before the column existed take `false`; every
    other row — another GitHub stream, the same stream name under another backend — keeps the
    default `true`. The backfill runs before `indexed` joins the revision trigger, so it leaves
    every revision where it was and nothing replays through the page-change consumers; from then on
    a flip is a revision and a same-value write is not. The downgrade returns the trigger to its
    prior column list and removes the column, and the restored trigger still fires on a body."""
    database = tmp_path / "indexed.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    pages = _seed(engine)
    revisions = _rows(engine, "revision")

    command.upgrade(config, REVISION)

    after = _rows(engine, "revision, indexed")
    assert {key: after[uid][1] for key, uid in pages.items()} == {
        ("github", "workflow_runs"): 0,
        ("github", "stargazers"): 0,
        ("github", "contributor_activity"): 0,
        ("github", "pull_requests"): 1,
        ("folder", "workflow_runs"): 1,
    }
    assert {uid: (row[0],) for uid, row in after.items()} == revisions

    flipped, held = pages[("github", "pull_requests")], pages[("folder", "workflow_runs")]
    top = max(row[0] for row in after.values())
    with engine.connect() as connection:
        connection.execute(
            sa.text("update page set indexed = 0 where uid = :uid"), {"uid": flipped}
        )
        connection.execute(sa.text("update page set indexed = 1 where uid = :uid"), {"uid": held})
        connection.commit()
    moved = _rows(engine, "revision")
    assert moved[flipped] == (top + 1,)
    assert moved[held] == (after[held][0],)

    command.downgrade(config, BEFORE)

    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(sa.text("pragma table_info(page)"))}
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text("select name from sqlite_master where type = 'trigger'")
            )
        }
        connection.execute(
            sa.text("update page set digest = 'sha256:edited' where uid = :uid"), {"uid": held}
        )
        connection.commit()
    assert "indexed" not in columns
    assert {"page_assign_revision_insert", "page_assign_revision_update"} <= triggers
    assert _rows(engine, "revision")[held] == (top + 2,)
