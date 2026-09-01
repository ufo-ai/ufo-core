from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

PREVIOUS_REVISION = "20260901073109"
AUTHORITY_REVISION = "20260901111145"


def test_turn_authority_migration_adds_and_removes_the_exact_constraint(tmp_path: Path) -> None:
    database = tmp_path / "turn-authority.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    assert "turn_authority" not in {
        constraint["name"] for constraint in sa.inspect(engine).get_check_constraints("turn")
    }

    command.upgrade(config, AUTHORITY_REVISION)
    assert "turn_authority" in {
        constraint["name"] for constraint in sa.inspect(engine).get_check_constraints("turn")
    }

    command.downgrade(config, PREVIOUS_REVISION)
    assert "turn_authority" not in {
        constraint["name"] for constraint in sa.inspect(engine).get_check_constraints("turn")
    }
    engine.dispose()
