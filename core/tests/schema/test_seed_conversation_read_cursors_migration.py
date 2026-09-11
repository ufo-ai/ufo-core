"""Every conversation a workspace already holds gets a read cursor per member of that workspace.

The cursor is the conversation's own activity moment, which is what keeps the rail quiet about
threads from before the column landed, and a cursor a member's own read already wrote stands. A
member of another workspace takes none. A cursor the running fleet writes between the filter and the
insert takes the key, so the seed passes it by instead of failing the migration.
"""

from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import command, op
from alembic.config import Config
from alembic.script import ScriptDirectory
from pytest import MonkeyPatch

from ufo.db import MIGRATIONS_DIR


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_the_seed_covers_every_member_and_leaves_a_written_cursor_alone(tmp_path: Path) -> None:
    database_path = tmp_path / "seed-conversation-read.db"
    config = _config(database_path)
    command.upgrade(config, "20260911102948")
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('v', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) values "
                "('m', 'w', 'one@example.com', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('n', 'w', 'two@example.com', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('o', 'v', 'three@example.com', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, reasoning, "
                "visibility, created_at, updated_at) values "
                "('a', 'w', 'main', 'p', 'auto', 'auto', 'workspace', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('b', 'v', 'main', 'p', 'auto', 'auto', 'workspace', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "audience, member_id, created_at, updated_at) values "
                "('c', 'w', 'a', 'web', 'c:1', 'shared', NULL, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('d', 'w', 'a', 'web', 'd:1', 'member:m', 'm', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('e', 'v', 'b', 'web', 'e:1', 'shared', NULL, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, terminal, created_at, updated_at) values "
                "('t', 'w', 'd', 'a', 1, 'done', 'build the homepage', '{}', "
                "'2026-01-02 03:04:05', '2026-01-02 03:04:05')"
            )
        )
        connection.execute(
            sa.text(
                "insert into conversation_read (workspace_id, conversation_id, member_id, "
                "read_at) values ('w', 'c', 'm', '2020-01-01 00:00:00')"
            )
        )
        connection.commit()
    command.upgrade(config, "20260911124440")
    with engine.connect() as connection:
        cursors = connection.execute(
            sa.text(
                "select workspace_id, conversation_id, member_id, read_at "
                "from conversation_read order by conversation_id, member_id"
            )
        ).all()
    engine.dispose()
    assert [(row[0], row[1], row[2]) for row in cursors] == [
        ("w", "c", "m"),
        ("w", "c", "n"),
        ("w", "d", "m"),
        ("w", "d", "n"),
        ("v", "e", "o"),
    ]
    assert str(cursors[0][3]).startswith("2020-01-01")
    assert not str(cursors[1][3]).startswith("2020-01-01")
    assert [str(row[3]) for row in cursors[2:4]] == ["2026-01-02 03:04:05"] * 2


def _seed_module(tmp_path: Path) -> ModuleType:
    script = ScriptDirectory.from_config(_config(tmp_path / "unused.db"))
    revision = script.get_revision("20260911124440")
    assert revision.module is not None
    return revision.module


def test_the_seed_yields_the_key_to_a_cursor_written_while_it_runs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    statements: list[str] = []

    class _Bind:
        dialect = sa.dialects.postgresql.dialect()

        def execute(self, statement: sa.Insert) -> None:
            statements.append(str(statement.compile(dialect=self.dialect)))

    monkeypatch.setattr(op, "get_bind", _Bind)
    _seed_module(tmp_path).upgrade()
    assert len(statements) == 1
    rendered = " ".join(statements[0].split())
    assert "ON CONFLICT (workspace_id, conversation_id, member_id) DO NOTHING" in rendered, rendered
