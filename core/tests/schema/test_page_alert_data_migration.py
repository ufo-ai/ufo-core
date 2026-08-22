from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def test_page_alert_data_migration_deletes_only_page_alert_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "page-alert-data.db"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", async_url)
    command.upgrade(config, "0057")
    workspace_id = uuid4()
    moment = datetime(2026, 7, 28, tzinfo=UTC)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": workspace_id.hex, "moment": moment},
        )
        for extension, key in (
            ("page_alerts", "watch:customer-renewal"),
            ("page_alerts", "page_change_cursor:dispatch"),
            ("memory", "page_change_cursor:index_pages"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:workspace, :extension, :key, '{}', :moment, :moment)"
                ),
                {
                    "workspace": workspace_id.hex,
                    "extension": extension,
                    "key": key,
                    "moment": moment,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "0058")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("select extension, key from ext_store order by extension, key")
        ).all()
    engine.dispose()
    assert rows == [("memory", "page_change_cursor:index_pages")]
