"""The backfill folds a workspace's turn-less spend on closed days and nothing else.

Spend a turn booked stays out of it, because the read still takes that from the ledger and a row
counted in both would bill twice. Today stays out because it is still being written.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260915111224"
AFTER = "20260915181110"
CLOSED_DAY = "2020-01-02 03:04:05"
WORKSPACE = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
CONVERSATION = "33333333-3333-4333-8333-333333333333"
TURN = "44444444-4444-4444-8444-444444444444"
LEDGER_COLUMNS = (
    "id, workspace_id, turn_id, dimension, amount, prompt_tokens, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_5m_tokens, cache_write_30m_tokens, cache_write_1h_tokens, "
    "priced_micro_usd, model, price_digest, created_at, updated_at"
)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _row(suffix: int, turn: str | None, amount: int, priced: int, created_at: str) -> str:
    booked = "NULL" if turn is None else f"'{turn}'"
    stamp = created_at if created_at == "CURRENT_TIMESTAMP" else f"'{created_at}'"
    return (
        f"('55555555-5555-4555-8555-55555555555{suffix}', '{WORKSPACE}', {booked}, 'tokens', "
        f"{amount}, {amount}, {amount}, 0, 0, 0, 0, 0, {priced}, 'claude-opus-4-8', 'digest', "
        f"{stamp}, {stamp})"
    )


def test_the_backfill_folds_closed_turn_less_days_only(tmp_path: Path) -> None:
    database_path = tmp_path / "fold-job-spend.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                f"('{WORKSPACE}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, reasoning, "
                "visibility, created_at, updated_at) values "
                f"('{AGENT}', '{WORKSPACE}', 'main', 'p', 'auto', 'auto', 'workspace', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "audience, created_at, updated_at) values "
                f"('{CONVERSATION}', '{WORKSPACE}', '{AGENT}', 'web', 'c:1', 'shared', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, terminal, created_at, updated_at) values "
                f"('{TURN}', '{WORKSPACE}', '{CONVERSATION}', '{AGENT}', 1, 'done', 'go', '{{}}', "
                f"'{CLOSED_DAY}', '{CLOSED_DAY}')"
            )
        )
        connection.execute(
            sa.text(
                f"insert into ledger ({LEDGER_COLUMNS}) values "
                + ", ".join(
                    (
                        _row(1, None, 100, 7, CLOSED_DAY),
                        _row(2, None, 200, 11, CLOSED_DAY),
                        _row(3, TURN, 800, 61, CLOSED_DAY),
                        _row(4, None, 400, 23, "CURRENT_TIMESTAMP"),
                    )
                )
            )
        )
        connection.commit()
    command.upgrade(config, AFTER)
    with engine.connect() as connection:
        folded = connection.execute(
            sa.text(
                "select workspace_id, day, dimension, model, price_digest, amount, "
                "priced_micro_usd, first_used_at from ledger_job_day order by day"
            )
        ).all()
    engine.dispose()
    assert [
        (UUID(str(row[0])), str(row[1]), row[2], row[3], row[4], row[5], row[6]) for row in folded
    ] == [(UUID(WORKSPACE), "2020-01-02", "tokens", "claude-opus-4-8", "digest", 300, 18)]
    assert str(folded[0][7]).startswith("2020-01-02 03:04:05")


def _module(tmp_path: Path) -> ModuleType:
    script = ScriptDirectory.from_config(_config(tmp_path / "unused.db"))
    revision = script.get_revision(AFTER)
    assert revision.module is not None
    return revision.module


def test_the_backfill_holds_a_day_open_for_the_same_margin_the_job_does(tmp_path: Path) -> None:
    """A deploy landing just after midnight must not close the day a still-open transaction will
    commit onto: `created_at` is that transaction's start. The job holds a day open for its settle
    interval and the backfill sets `rolled_through`, so a backfill without the margin closes a day
    no later run revisits."""
    module = _module(tmp_path)
    midnight = datetime(2026, 9, 15, tzinfo=UTC)
    straggler = midnight - timedelta(minutes=2)
    assert module.settled_ceiling(midnight + timedelta(minutes=1)) <= straggler
    assert module.settled_ceiling(midnight + timedelta(hours=1)) > straggler
