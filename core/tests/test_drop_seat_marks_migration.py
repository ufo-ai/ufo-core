"""0106 takes the seat shipper's day marks with it.

Nothing ships a member count any more, so the marks that stopped the retired job shipping twice in
one day are rows no code will ever read or clear again. Absence alone could not be the trigger — an
extension that merely failed to import would look identical — so the migration that removes the job
removes its rows. Another extension's cursor stays, because a delete keyed on the extension is the
only thing that keeps a tear-out from reaching past itself.
"""

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


def test_the_seat_marks_go_and_another_extension_keeps_its_own(tmp_path: Path) -> None:
    database = tmp_path / "seat-marks.db"
    config = _config(database)
    command.upgrade(config, "0105")
    workspace_id = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": str(workspace_id), "now": NOW},
        )
        for extension, key in (
            ("metronome", "seats_shipped_date"),
            ("metronome", "ship_floor"),
            ("memory", "page_cursor"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:workspace_id, :extension, :key, :value, :now, :now)"
                ),
                {
                    "workspace_id": str(workspace_id),
                    "extension": extension,
                    "key": key,
                    "value": '"2026-08-17"',
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "0106")

    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        store = connection.execute(sa.text("select extension, key from ext_store")).all()
    engine.dispose()

    assert set(store) == {("metronome", "ship_floor"), ("memory", "page_cursor")}
