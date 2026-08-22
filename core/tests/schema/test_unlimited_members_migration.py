"""0084 makes the hosted plan's unlimited members true of the rows that already exist.

A workspace the seat shipper reached carries 25 and 5, so its sixth member is unseated and refused.
Both columns go, because nothing writes or reads one any more; seating everyone is the other half,
because `seated_at` is now the whole answer to whether the agent answers a member — a row the
retired bound left unseated would read as an admin's deliberate revocation and stay unanswered, and
every read that picks a seated admin would skip them. The retired approval job's markers go with
it, and another extension's cursor stays.

Dropping a column rebuilds the table on SQLite, so the page-revision triggers are proven to still
fire afterwards: their bodies name `workspace`, and a rebuild that left them rewritten would break
every page write on a deploy the migration reported as green.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

NOW = datetime(2026, 8, 13, tzinfo=UTC)
JOINED = datetime(2026, 6, 1, tzinfo=UTC)


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(path: Path) -> tuple[Config, dict[str, UUID]]:
    """Two workspaces the moment before 0082: one the seat shipper bounded, holding a seated admin
    and a joiner it refused, and one that never met a billing extension."""
    config = _config(path)
    command.upgrade(config, "0083")
    ids = {
        "bounded": uuid4(),
        "unbounded": uuid4(),
        "admin": uuid4(),
        "joiner": uuid4(),
        "solo": uuid4(),
    }
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        for workspace, limit, included in ((ids["bounded"], 25, 5), (ids["unbounded"], None, None)):
            connection.execute(
                sa.text(
                    "insert into workspace "
                    "(id, seat_limit, included_seats, created_at, updated_at) "
                    "values (:id, :limit, :included, :now, :now)"
                ),
                {
                    "id": workspace.hex,
                    "limit": limit,
                    "included": included,
                    "now": NOW,
                },
            )
        for member, workspace, is_admin, seated in (
            (ids["admin"], ids["bounded"], True, JOINED),
            (ids["joiner"], ids["bounded"], False, None),
            (ids["solo"], ids["unbounded"], True, JOINED),
        ):
            connection.execute(
                sa.text(
                    "insert into member (id, workspace_id, email, is_admin, seated_at, "
                    "created_at, updated_at) "
                    "values (:id, :ws, :email, :admin, :seated, :joined, :now)"
                ),
                {
                    "id": member.hex,
                    "ws": workspace.hex,
                    "email": f"{member.hex[:8]}@example.com",
                    "admin": is_admin,
                    "seated": seated,
                    "joined": JOINED,
                    "now": NOW,
                },
            )
        for extension, key in (
            ("metronome", "seat_approval_asked/late@example.com"),
            ("metronome", "seats_shipped_date"),
            ("memory", "page_cursor"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:ws, :extension, :key, '{}', :now, :now)"
                ),
                {
                    "ws": ids["bounded"].hex,
                    "extension": extension,
                    "key": key,
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()
    return config, ids


def test_clearing_the_bounds_seats_every_member_and_drops_the_approval_markers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unlimited-members.db"
    config, ids = _seed(database)

    command.upgrade(config, "0084")

    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        columns = {
            row.name for row in connection.execute(sa.text("pragma table_info(workspace)")).all()
        }
        members = {
            UUID(row.id): row.seated_at
            for row in connection.execute(sa.text("select id, seated_at from member")).all()
        }
        store = connection.execute(sa.text("select extension, key from ext_store")).all()
        revision = _revision_after_a_page_write(connection, ids["bounded"])
    engine.dispose()

    assert columns == {"id", "page_revision", "created_at", "updated_at"}
    assert revision == (1, 1)
    assert members[ids["joiner"]] is not None
    already_seated = members[ids["admin"]]
    assert already_seated is not None
    assert already_seated == members[ids["solo"]]
    assert set(store) == {("metronome", "seats_shipped_date"), ("memory", "page_cursor")}


def _revision_after_a_page_write(connection: sa.Connection, workspace_id: UUID) -> tuple[int, int]:
    """The workspace's page revision and the page's own, after one page is written through the
    triggers the column drop rebuilt the table under."""
    source_id, page_id = uuid4(), uuid4()
    connection.execute(
        sa.text(
            "insert into source (id, workspace_id, backend, config, next_sync_at, "
            "created_at, updated_at) "
            "values (:id, :ws, 'test', '{}', :now, :now, :now)"
        ),
        {"id": source_id.hex, "ws": workspace_id.hex, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into page (id, workspace_id, source_id, digest, body_ref, subject, "
            "tombstone, created_at, updated_at) "
            "values (:id, :ws, :source, 'd', 'b', 'shared', 0, :now, :now)"
        ),
        {"id": page_id.hex, "ws": workspace_id.hex, "source": source_id.hex, "now": NOW},
    )
    connection.commit()
    return (
        connection.execute(
            sa.text("select page_revision from workspace where id = :id"),
            {"id": workspace_id.hex},
        ).scalar_one(),
        connection.execute(
            sa.text("select revision from page where id = :id"), {"id": page_id.hex}
        ).scalar_one(),
    )
