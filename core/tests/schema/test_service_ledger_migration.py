from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import MIGRATIONS_DIR, workspace_tx
from ufo.schema import tables

SERVICE_LEDGER = "20261006230332"
UNSERVICED_INSERT = sa.text(
    "insert into ledger (id, workspace_id, turn_id, dimension, amount, priced_micro_usd, model, "
    "created_at, updated_at) values (:id, :workspace_id, :turn_id, :dimension, 1, 0, '', :now, "
    ":now)"
).bindparams(
    sa.bindparam("id", type_=sa.Uuid()),
    sa.bindparam("workspace_id", type_=sa.Uuid()),
    sa.bindparam("turn_id", type_=sa.Uuid()),
    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
)


async def _turn(connection: AsyncConnection) -> tuple[UUID, UUID]:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="claude-opus-4-8",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key="session",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="queued",
            inbound="hi",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_a_row_inserted_with_no_service_takes_the_service_of_its_dimension(
    db: None,
) -> None:
    now = datetime.now(UTC)
    tokens, sandbox, egress, probe = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _turn(connection)
        for ledger_id, turn, dimension in (
            (tokens, None, "tokens"),
            (sandbox, turn_id, "sandbox_tokens"),
            (egress, turn_id, "egress"),
            (probe, None, "egress"),
        ):
            await connection.execute(
                UNSERVICED_INSERT,
                {
                    "id": ledger_id,
                    "workspace_id": workspace_id,
                    "turn_id": turn,
                    "dimension": dimension,
                    "now": now,
                },
            )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.labels,
                    tables.ledger.c.byok,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    assert {row.id: tuple(row)[1:] for row in rows} == {
        tokens: ("models", "tokens", {}, None),
        sandbox: ("models", "tokens", {"via": "proxy", "turn": str(turn_id)}, False),
        egress: ("proxy", "requests", {"turn": str(turn_id)}, None),
        probe: ("proxy", "requests", {}, None),
    }


def test_the_upgrade_files_folded_days_under_their_service_and_keeps_the_ledger_checks(
    tmp_path: Path,
) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    before = ScriptDirectory.from_config(config).get_revision(SERVICE_LEDGER).down_revision
    assert isinstance(before, str)
    command.upgrade(config, before)
    workspace_id, now = uuid4(), datetime.now(UTC)
    folded = {uuid4(): dimension for dimension in ("tokens", "sandbox_tokens", "egress", "images")}
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    with engine.connect() as connection:
        for day_id, dimension in folded.items():
            connection.execute(
                sa.text(
                    "insert into ledger_job_day (id, workspace_id, day, dimension, model, amount, "
                    "priced_micro_usd, first_used_at, created_at, updated_at) values (:id, "
                    ":workspace_id, :day, :dimension, '', 1, 0, :now, :now, :now)"
                ).bindparams(
                    sa.bindparam("id", type_=sa.Uuid()),
                    sa.bindparam("workspace_id", type_=sa.Uuid()),
                    sa.bindparam("day", type_=sa.Date()),
                    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
                ),
                {
                    "id": day_id,
                    "workspace_id": workspace_id,
                    "day": now.date(),
                    "dimension": dimension,
                    "now": now,
                },
            )
        connection.commit()
    command.upgrade(config, SERVICE_LEDGER)
    with engine.connect() as connection:
        filed = {
            row.id: (row.service, row.dimension)
            for row in connection.execute(
                sa.select(
                    tables.ledger_job_day.c.id,
                    tables.ledger_job_day.c.service,
                    tables.ledger_job_day.c.dimension,
                )
            )
        }
        with pytest.raises(sa.exc.IntegrityError, match="ledger_amount"):
            connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    service="models",
                    dimension="tokens",
                    amount=0,
                    priced_micro_usd=0,
                    model="",
                    created_at=now,
                    updated_at=now,
                )
            )
    engine.dispose()
    assert {dimension: filed[day_id] for day_id, dimension in folded.items()} == {
        "tokens": ("models", "tokens"),
        "sandbox_tokens": ("models", "tokens"),
        "egress": ("proxy", "requests"),
        "images": ("models", "images"),
    }
