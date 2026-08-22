from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def test_removing_exa_deletes_only_its_durable_rows(tmp_path: Path) -> None:
    path = tmp_path / "exa-removal.db"
    config = _config(path)
    command.upgrade(config, "0099")
    workspace_id = uuid4().hex
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id, "now": NOW},
        )
        for extension in ("exa", "memory"):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:workspace_id, :extension, 'state', '{}', :now, :now)"
                ),
                {"workspace_id": workspace_id, "extension": extension, "now": NOW},
            )
        for slot in ("exa_api_key", "perplexity_api_key"):
            connection.execute(
                sa.text(
                    "insert into credential "
                    "(workspace_id, slot, ciphertext, created_at, updated_at) "
                    "values (:workspace_id, :slot, :ciphertext, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id,
                    "slot": slot,
                    "ciphertext": b"sealed",
                    "now": NOW,
                },
            )
        connection.commit()

    command.upgrade(config, "0100")

    with engine.connect() as connection:
        assert connection.execute(sa.text("select extension from ext_store")).scalars().all() == [
            "memory"
        ]
        assert connection.execute(sa.text("select slot from credential")).scalars().all() == [
            "perplexity_api_key"
        ]
    engine.dispose()
