"""The daily brief tear-out closes its branch and leaves its tables standing.

Deployed databases stamped `sweep_0002`, and alembic resolves every stamped head from the script
directory before it runs any DDL, so a head whose files left the tree stops the migrate Job the
deploy waits on. The revision merges that branch into core's line, and the version table must come
out holding core's head alone.

The tables outlive it on purpose. The migrate Job completes before the fleet rolls, so the outgoing
pods still carry the sweep extension, whose gating `pre_tool_use` hook reads `sweep_application` on
every scheduled turn that touches `update_todo_list`, `memory_update`, or `set_homepage`. Dropping
either table here would fail that hook closed and refuse those turns for the whole rollout, so the
drop belongs to a later revision and both tables must survive this one with their rows."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

NOW = datetime(2026, 8, 23, tzinfo=UTC)
MERGE = "20260823211339"
DROP = "20260823223019"
CORE_PARENT = "20260823021954"
SWEEP_HEAD = "sweep_0002"


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(path: Path) -> tuple[Config, dict[str, UUID]]:
    """A workspace as it stands the moment before the merge: core at its own head, sweep at its
    branch head, and one member holding a completed edition and its registered application."""
    config = _config(path)
    command.upgrade(config, SWEEP_HEAD)
    command.upgrade(config, CORE_PARENT)
    ids = {"workspace": uuid4(), "member": uuid4(), "agent": uuid4(), "conversation": uuid4()}
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": ids["workspace"].hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, is_admin, created_at, updated_at) "
                "values (:id, :ws, 'member@work.com', false, :now, :now)"
            ),
            {"id": ids["member"].hex, "ws": ids["workspace"].hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "internet_access_allowed, created_at, updated_at) "
                "values (:id, :ws, 'brief', 'p', 'auto', false, false, :now, :now)"
            ),
            {"id": ids["agent"].hex, "ws": ids["workspace"].hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "created_at, updated_at) "
                "values (:id, :ws, :agent, 'web', :id, :now, :now)"
            ),
            {
                "id": ids["conversation"].hex,
                "ws": ids["workspace"].hex,
                "agent": ids["agent"].hex,
                "now": NOW,
            },
        )
        connection.execute(
            sa.text(
                "insert into sweep_edition (workspace_id, member_id, local_date, timezone, "
                "status, created_at, updated_at) values (:ws, :member, '2026-08-20', 'UTC', "
                "'completed', :now, :now)"
            ),
            {"ws": ids["workspace"].hex, "member": ids["member"].hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into sweep_application (workspace_id, conversation_id, member_id, "
                "agent_id, created_at, updated_at) values (:ws, :conv, :member, :agent, :now, :now)"
            ),
            {
                "ws": ids["workspace"].hex,
                "conv": ids["conversation"].hex,
                "member": ids["member"].hex,
                "agent": ids["agent"].hex,
                "now": NOW,
            },
        )
        connection.commit()
    engine.dispose()
    return config, ids


def _heads(path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        heads = {
            row[0] for row in connection.execute(sa.text("select version_num from alembic_version"))
        }
    engine.dispose()
    return heads


def _tables(path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                sa.text("select name from sqlite_master where type = 'table'")
            )
        }
    engine.dispose()
    return names


def test_the_branch_head_is_retired(tmp_path: Path) -> None:
    path = tmp_path / "brief.sqlite"
    config, _ids = _seed(path)
    assert _heads(path) == {CORE_PARENT, SWEEP_HEAD}

    command.upgrade(config, MERGE)

    assert _heads(path) == {MERGE}


def test_both_tables_and_their_rows_outlive_the_merge(tmp_path: Path) -> None:
    """What the outgoing image still reads through its gating hook."""
    path = tmp_path / "rollout.sqlite"
    config, _ids = _seed(path)

    command.upgrade(config, MERGE)

    assert {"sweep_edition", "sweep_application"} <= _tables(path)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert (
            connection.execute(sa.text("select count(*) from sweep_application")).scalar_one() == 1
        )
        assert connection.execute(sa.text("select count(*) from sweep_edition")).scalar_one() == 1
    engine.dispose()


def test_the_later_revision_drops_both_tables(tmp_path: Path) -> None:
    """Once no running image reads them, the rows answer to no code."""
    path = tmp_path / "drop.sqlite"
    config, _ids = _seed(path)

    command.upgrade(config, "heads")

    assert _heads(path) == {DROP}
    assert not {"sweep_edition", "sweep_application"} & _tables(path)


def test_the_workspace_the_brief_belonged_to_survives(tmp_path: Path) -> None:
    path = tmp_path / "neighbours.sqlite"
    config, ids = _seed(path)

    command.upgrade(config, "heads")

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert connection.execute(sa.text("select count(*) from workspace")).scalar_one() == 1
        assert connection.execute(sa.text("select count(*) from member")).scalar_one() == 1
        assert (
            connection.execute(
                sa.text("select name from agent where id = :id"), {"id": ids["agent"].hex}
            ).scalar_one()
            == "brief"
        )
        assert connection.execute(sa.text("select count(*) from conversation")).scalar_one() == 1
    engine.dispose()
