"""The coding app adopts the agent the coding extension shipped, and gives it back on a downgrade.

The reviewer is not a new agent, so the move is an identity change and nothing else: the row's
name, its prompt, its owner and every edge hanging off it stand exactly as they were.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260825023542"
ADOPT = "20260825044912"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(database_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('y', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('z', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, visibility, "
                "provisioned_by, provisioned_name, provisioned_version, created_at, updated_at) "
                "values "
                "('a', 'w', 'code-review', 'review it', 'auto', 0, 'private', "
                "'coding', 'code-review', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'x', 'code-review-narrowed', 'review it', 'auto', 0, 'workspace', "
                "'coding', 'code-review', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'w', 'radar', 'p', 'auto', 0, 'workspace', "
                "'app_radar', 'radar', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('e', 'z', 'code-review', 'review it', 'auto', 0, 'private', "
                "'coding', 'code-review', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('f', 'z', 'code', 'mine', 'auto', 0, 'private', "
                "null, null, null, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, prompt, "
                "model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "('d', 'y', '~archived-d', 'code-review-coding', CURRENT_TIMESTAMP, 'review it', "
                "'auto', 0, 'private', 'coding', 'code-review', '0.1.0', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    return engine


def _provenance(engine: sa.Engine) -> list[tuple[str, str, str, str]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text("select id, name, provisioned_by, provisioned_name from agent order by id")
            ).all()
        ]


def test_the_reviewer_moves_extension_and_keeps_everything_else(tmp_path: Path) -> None:
    """The homepage read keys an agent by the slug in its `provisioned_by`, so that half of the
    identity is what has to move for the page to reach this agent at all, and the declared name goes
    with it: the app is named for the work it operates over, like every other default app.

    The row's own name follows, but only where the workspace never answered otherwise. A member who
    renamed their reviewer keeps their name, a workspace already holding something else called
    `code` keeps both rows, and no other extension's rows are touched. `coding` is not the name
    either way — it is the reviewers' own profile name in the spawn namespace, so an agent under it
    would be an ambiguous spawn target."""
    database_path = tmp_path / "adopt.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, ADOPT)
    assert _provenance(engine) == [
        # The shipped row takes the app's name.
        ("a", "code", "app_code", "code"),
        ("b", "radar", "app_radar", "radar"),
        # A member's own name for the row stands.
        ("c", "code-review-narrowed", "app_code", "code"),
        # An archived row moves and stays archived under the name it was put away as.
        ("d", "~archived-d", "app_code", "code"),
        # `code` is taken in this workspace, so the reviewer keeps the name it had.
        ("e", "code-review", "app_code", "code"),
        ("f", "code", None, None),
    ]
    with engine.connect() as connection:
        held = connection.execute(
            sa.text("select prompt, provisioned_version from agent where id = 'a'")
        ).one()
    assert held == ("review it", "0.1.0")
    engine.dispose()


def test_the_adopted_reviewer_becomes_visible_as_an_app_is(tmp_path: Path) -> None:
    """The release that shipped the reviewer declared no visibility, so every row took the default
    and is private — invisible to every member who is not an admin, page and all. This extension
    declares `workspace`, which is what an app is, and no provisioning pass would ever carry that.

    Only a row still at that default moves. A member who narrowed their own reviewer on purpose
    keeps it narrow, because a migration carries a newly declared field onto the rows that never
    stated one, never over a member's own answer — and the downgrade narrows nothing back, for the
    same reason read the other way round.

    An archived row is left at its visibility too. Archiving the shipped reviewer is how a
    workspace says it does not want one, and widening what a member put away would hand it back
    visible to everyone the day they restored it. Its identity still moves, which is what keeps it
    saying that under the new name — the testing fleet holds exactly this row."""
    database_path = tmp_path / "adopt-visibility.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, ADOPT)
    assert _visibility(engine) == [
        ("a", "workspace"),
        ("b", "workspace"),
        ("c", "workspace"),
        ("d", "private"),
        ("e", "workspace"),
        ("f", "private"),
    ]

    command.downgrade(config, BEFORE)
    # Nothing narrows back. A row reading `workspace` here is either one the upgrade widened or one
    # a member widened themselves, and nothing on the row tells the two apart — so narrowing them
    # all would take away a member's own answer to make the rollback tidy.
    assert _visibility(engine) == [
        ("a", "workspace"),
        ("b", "workspace"),
        ("c", "workspace"),
        ("d", "private"),
        ("e", "workspace"),
        ("f", "private"),
    ]
    engine.dispose()


def _visibility(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text("select id, visibility from agent order by id")
            ).all()
        ]


def test_a_downgrade_gives_the_agent_back(tmp_path: Path) -> None:
    database_path = tmp_path / "adopt-down.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, ADOPT)
    command.downgrade(config, BEFORE)
    assert _provenance(engine) == [
        ("a", "code-review", "coding", "code-review"),
        ("b", "radar", "app_radar", "radar"),
        ("c", "code-review-narrowed", "coding", "code-review"),
        ("d", "~archived-d", "coding", "code-review"),
        ("e", "code-review", "coding", "code-review"),
        ("f", "code", None, None),
    ]
    engine.dispose()
