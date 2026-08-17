from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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


def test_0101_accepts_writes_without_token_class_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger-usage.db"
    config = _config(database_path)
    command.upgrade(config, "0098")
    moment = datetime.now(UTC)
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    ledger_id = uuid4()
    egress_id = uuid4()
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": moment},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :workspace, :email, :now, :now)"
            ),
            {"id": member_id.hex, "workspace": workspace_id.hex, "email": "a@b.c", "now": moment},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
                "values (:id, :workspace, :name, :prompt, :model, :now, :now)"
            ),
            {
                "id": agent_id.hex,
                "workspace": workspace_id.hex,
                "name": "assistant",
                "prompt": "p",
                "model": "claude-opus-4-8",
                "now": moment,
            },
        )
        connection.execute(
            sa.text(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "member_id, audience, created_at, updated_at) values (:id, :workspace, :agent, "
                "'cli', 'session', :member, :audience, :now, :now)"
            ),
            {
                "id": conversation_id.hex,
                "workspace": workspace_id.hex,
                "agent": agent_id.hex,
                "member": member_id.hex,
                "audience": f"member:{member_id}",
                "now": moment,
            },
        )
        connection.execute(
            sa.text(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, byok, created_at, updated_at) values (:id, :workspace, :conversation, "
                ":agent, 1, 'queued', 'hi', 1, :now, :now)"
            ),
            {
                "id": turn_id.hex,
                "workspace": workspace_id.hex,
                "conversation": conversation_id.hex,
                "agent": agent_id.hex,
                "now": moment,
            },
        )
        connection.execute(
            sa.text(
                "insert into ledger (id, workspace_id, turn_id, dimension, amount, prompt_tokens, "
                "cache_read_tokens, priced_micro_usd, model, created_at, updated_at) values "
                "(:id, :workspace, :turn, 'tokens', 100, 100, 0, 500, "
                "'claude-opus-4-8', :now, :now)"
            ),
            {
                "id": ledger_id.hex,
                "workspace": workspace_id.hex,
                "turn": turn_id.hex,
                "now": moment,
            },
        )
        connection.execute(
            sa.text(
                "insert into ledger (id, workspace_id, turn_id, dimension, amount, prompt_tokens, "
                "cache_read_tokens, priced_micro_usd, model, created_at, updated_at) values "
                "(:id, :workspace, :turn, 'egress', 1, 0, 0, 0, '', :now, :now)"
            ),
            {
                "id": egress_id.hex,
                "workspace": workspace_id.hex,
                "turn": turn_id.hex,
                "now": moment,
            },
        )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "0101")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        migrated = connection.execute(
            sa.text(
                "select input_tokens, output_tokens, byok, token_classes_complete "
                "from ledger where id = :id"
            ),
            {"id": ledger_id.hex},
        ).one()
        assert tuple(migrated) == (100, 0, True, False)
        egress_byok = connection.execute(
            sa.text("select byok from ledger where id = :id"),
            {"id": egress_id.hex},
        ).scalar_one()
        assert egress_byok is None
        connection.execute(
            sa.text(
                "insert into ledger (id, workspace_id, turn_id, dimension, amount, prompt_tokens, "
                "cache_read_tokens, priced_micro_usd, model, created_at, updated_at) values "
                "(:id, :workspace, :turn, 'tokens', 100, 100, 0, 500, "
                "'claude-opus-4-8', :now, :now)"
            ),
            {"id": uuid4().hex, "workspace": workspace_id.hex, "turn": turn_id.hex, "now": moment},
        )
        connection.commit()
    engine.dispose()
