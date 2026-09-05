"""A model-spec tear-out takes its rows with it.

`agent.model` is free text with no foreign key, and the registry raises on an unknown id at turn
setup — before the intent dispatch that serves the portal's own model-change form. So an agent left
naming a retired spec cannot be repaired from inside the product.

The reasoning field rides along: glm-5.2 could disable reasoning and glm-5.3 cannot, so a row
carried across with `off` intact is a pair `_known_model` refuses, and the refusal lands on every
later apply of that agent — including the one that would repair it. Every other agent's model and
reasoning stay exactly as they were.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_agents_on_glm_5_2_move_to_5_3_and_lose_a_reasoning_5_3_refuses(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "retire-glm-5-2.db"
    config = _config(database_path)
    command.upgrade(config, "20260903020306")
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, reasoning, "
                "created_at, updated_at) values "
                "('a', 'w', 'retired', 'p', 'z-ai/glm-5.2', 'off', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('b', 'w', 'retired-medium', 'p', 'z-ai/glm-5.2', 'medium', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('c', 'w', 'kept', 'p', 'z-ai/glm-5.3', 'auto', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('d', 'w', 'flash', 'p', 'z-ai/glm-5.3-flash', 'auto', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('e', 'w', 'auto-off', 'p', 'auto', 'off', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "20260904230515")
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("select id, model, reasoning from agent order by id")
        ).all()
    engine.dispose()
    assert rows == [
        ("a", "z-ai/glm-5.3", "low"),
        ("b", "z-ai/glm-5.3", "medium"),
        ("c", "z-ai/glm-5.3", "auto"),
        ("d", "z-ai/glm-5.3-flash", "auto"),
        ("e", "auto", "off"),
    ]
