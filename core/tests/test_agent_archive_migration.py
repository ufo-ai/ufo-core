from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

REVISION = "20260820015537"
RELEASE_REVISION = "20260823202415"
PRIOR = "20260823021954"
WORKSPACE = (
    "insert into workspace (id, created_at, updated_at) values "
    "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)
AGENT = (
    "insert into agent (id, workspace_id, name, prompt, model, is_main, provisioned_by, "
    "provisioned_name, provisioned_version, archived_at, created_at, updated_at) values "
    "(:id, 'w', :name, 'p', 'auto', :main, :by, :provisioned, :version, :archived, "
    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _agent(
    name: str,
    identifier: str,
    *,
    archived: bool = False,
    main: bool = False,
    provisioned: str | None = None,
) -> sa.TextClause:
    return sa.text(AGENT).bindparams(
        id=identifier,
        name=name,
        main=1 if main else 0,
        by=provisioned,
        provisioned=None if provisioned is None else name,
        version=None if provisioned is None else "1",
        archived="2026-08-20 00:00:00" if archived else None,
    )


def test_archive_keeps_the_name_constraint_and_full_conflict_target_valid(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-archive.db"
    config = _config(database_path)
    command.upgrade(config, PRIOR)
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(sa.text(WORKSPACE))
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "created_at, updated_at) values "
                "('a', 'w', 'invoice-intake', 'p', 'auto', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        connection.execute(
            sa.text("update agent set archived_at = '2026-08-20 00:00:00' where id = :id"),
            {"id": "a"},
        )
        connection.commit()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(_agent("invoice-intake", "b"))
            connection.commit()
        connection.rollback()
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "created_at, updated_at) values "
                "('b', 'w', 'invoice-intake', 'p', 'auto', 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP) on conflict (workspace_id, name) do nothing"
            )
        )
        connection.commit()
        rows = connection.execute(sa.text("select id from agent order by id")).all()
    engine.dispose()
    assert rows == [("a",)]


def test_the_main_agent_cannot_be_archived(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-archive-scope.db"
    config = _config(database_path)
    command.upgrade(config, REVISION)
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(sa.text(WORKSPACE))
        connection.commit()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(_agent("ufo", "m", main=True, archived=True))
            connection.commit()
        connection.rollback()
        connection.execute(_agent("briefer", "s", provisioned="brief_pipeline", archived=True))
        connection.commit()
        archived = connection.execute(sa.text("select archived_at from agent where id = 's'"))
        assert archived.scalar_one() is not None
    engine.dispose()


def test_archived_rows_release_their_names_without_removing_the_full_constraint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "free-archived-agent-name.db"
    config = _config(database_path)
    command.upgrade(config, REVISION)
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(sa.text(WORKSPACE))
        connection.execute(_agent("invoice-intake", "a", archived=True))
        connection.commit()
    command.upgrade(config, RELEASE_REVISION)
    with engine.connect() as connection:
        archived = connection.execute(
            sa.text("select name, archived_name from agent where id = 'a'")
        ).one()
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "created_at, updated_at) values "
                "('b', 'w', 'invoice-intake', 'p', 'auto', 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP) on conflict (workspace_id, name) do nothing"
            )
        )
        connection.commit()
        rows = connection.execute(sa.text("select id, name from agent order by id")).all()
    engine.dispose()
    assert archived == ("~archived-a", "invoice-intake")
    assert rows == [("a", "~archived-a"), ("b", "invoice-intake")]
