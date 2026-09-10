import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

BEFORE = "20260909233938"
REVISION = "20260910101300"
NOW = "2026-09-10 06:30:00+00:00"
SLOT = "openai_api_key"
WITH_MEMBER = (
    "insert into credential_fulfillment (workspace_id, request_id, slot, member_id, "
    f"fulfilled_at) values (:ws, :request, '{SLOT}', :member, :now)"
)
WITHOUT_MEMBER = (
    "insert into credential_fulfillment (workspace_id, request_id, slot, fulfilled_at) "
    f"values (:ws, :request, '{SLOT}', :now)"
)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(connection: sa.Connection) -> dict[str, str]:
    ids = {key: uuid4().hex for key in ("ws", "member", "request")}
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": ids["ws"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into member (id, workspace_id, email, is_admin, seated_at, created_at, "
            "updated_at) values (:id, :ws, 'admin@work.com', 1, :now, :now, :now)"
        ),
        {"id": ids["member"], "ws": ids["ws"], "now": NOW},
    )
    connection.execute(
        sa.text(WITH_MEMBER),
        {"ws": ids["ws"], "request": ids["request"], "member": ids["member"], "now": NOW},
    )
    connection.commit()
    return ids


def test_a_fulfillment_lands_with_no_member_and_the_replaced_image_still_names_one(
    tmp_path: Path,
) -> None:
    database = tmp_path / "credential-fulfillment.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        ids = _seed(connection)
    command.upgrade(config, REVISION)
    unwritten, replaced = uuid4().hex, uuid4().hex
    with engine.connect() as connection:
        connection.execute(sa.text("pragma foreign_keys = on"))
        connection.execute(
            sa.text(WITHOUT_MEMBER), {"ws": ids["ws"], "request": unwritten, "now": NOW}
        )
        connection.execute(
            sa.text(WITH_MEMBER),
            {"ws": ids["ws"], "request": replaced, "member": ids["member"], "now": NOW},
        )
        connection.commit()
        members = dict(
            connection.execute(
                sa.text("select request_id, member_id from credential_fulfillment")
            ).all()
        )
    engine.dispose()

    assert members == {
        ids["request"]: ids["member"],
        unwritten: None,
        replaced: ids["member"],
    }


def test_the_downgrade_refuses_while_a_fulfillment_carries_no_member(tmp_path: Path) -> None:
    database = tmp_path / "credential-fulfillment-downgrade.db"
    config = _config(database)
    command.upgrade(config, REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        ids = _seed(connection)
        connection.execute(
            sa.text(WITHOUT_MEMBER), {"ws": ids["ws"], "request": uuid4().hex, "now": NOW}
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="carry no member"):
        command.downgrade(config, BEFORE)

    with engine.connect() as connection:
        connection.execute(sa.text("delete from credential_fulfillment where member_id is null"))
        connection.commit()
    command.downgrade(config, BEFORE)
    with engine.connect() as connection:
        columns = {
            column["name"]: column["nullable"]
            for column in sa.inspect(connection).get_columns("credential_fulfillment")
        }
        query = connection.execute(sa.text("select member_id from credential_fulfillment"))
        members = query.scalars().all()
    engine.dispose()

    assert columns["member_id"] is False
    assert members == [ids["member"]]
