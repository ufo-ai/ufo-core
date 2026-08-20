"""The revision gives the member row the two facts an invitation states: when they were added,
and by whom.

Every member that predates the revision arrived without an invitation, so the columns are null on
each of them — the delivery reads the stamp as the whole event, and a backfilled instant would mail
a workspace's entire roster."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(database_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, is_admin, created_at, updated_at) "
                "values ('a', 'w', 'admin@acme.com', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('t', 'w', 'teammate@acme.com', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    engine.dispose()


def test_the_revision_stamps_nobody_and_takes_the_admin_who_adds_the_next_member(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "member-invitation-stamp.db"
    config = _config(database_path)
    command.upgrade(config, "20260819191916")
    _seed(database_path)

    command.upgrade(config, "20260820010508")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        standing = connection.execute(
            sa.text("select id, invited_at, invited_by from member order by id")
        ).all()
        assert standing == [("a", None, None), ("t", None, None)]
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, is_admin, invited_at, invited_by, "
                "created_at, updated_at) values "
                "('n', 'w', 'newhire@acme.com', 0, '2026-08-18 10:00:00', 'a', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
        added = connection.execute(
            sa.text(
                "select invited.email, inviter.email from member invited "
                "join member inviter on inviter.id = invited.invited_by "
                "where invited.invited_at is not null"
            )
        ).all()
    engine.dispose()
    assert added == [("newhire@acme.com", "admin@acme.com")]


def test_the_revision_downgrades_to_a_member_row_carrying_neither_column(tmp_path: Path) -> None:
    database_path = tmp_path / "member-invitation-stamp-down.db"
    config = _config(database_path)
    command.upgrade(config, "20260820010508")
    _seed(database_path)

    command.downgrade(config, "20260819191916")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    columns = {column["name"] for column in sa.inspect(engine).get_columns("member")}
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select id from member order by id")).all()
        with pytest.raises(sa.exc.OperationalError):
            connection.execute(sa.text("select invited_at from member"))
    engine.dispose()
    assert columns.isdisjoint({"invited_at", "invited_by"})
    assert rows == [("a",), ("t",)]
