from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR

FIRST = """
import sqlalchemy as sa
from alembic import op

revision = "scratch_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("scratch_probe", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade() -> None:
    op.drop_table("scratch_probe")
"""
SECOND = """
revision = "scratch_0002"
down_revision = "scratch_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError("the second revision refuses")


def downgrade() -> None:
    pass
"""


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "per-migration.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_per_migration_{uuid4().hex[:8]}"
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


def test_a_revision_that_refuses_leaves_the_one_before_it_applied(
    migration_urls: tuple[str, str], tmp_path: Path
) -> None:
    """Every revision commits on its own: a chain whose second revision raises leaves the first
    applied and stamped, so a data revision holds no lock into the DDL revision behind it and a
    refusal names exactly the revision that refused."""
    migration_url, sync_url = migration_urls
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "0001_probe.py").write_text(FIRST)
    (scratch / "0002_refuses.py").write_text(SECOND)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(scratch))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

    with pytest.raises(RuntimeError, match="the second revision refuses"):
        command.upgrade(config, "heads")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        tables = set(sa.inspect(connection).get_table_names())
        stamped = connection.execute(sa.text("select version_num from alembic_version")).all()
    engine.dispose()
    assert "scratch_probe" in tables
    assert [row.version_num for row in stamped] == ["scratch_0001"]
